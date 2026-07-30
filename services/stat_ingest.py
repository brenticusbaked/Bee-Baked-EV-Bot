"""Contextual Stat Enrichment Engine — daily box-score ingestion & historical backfill.

Fetches player box scores from free stat libraries (MLB StatsAPI, ESPN for NBA/WNBA/NFL,
soccerdata, Sackmann tennis) and upserts them into Supabase *_player_logs via db_manager.
Features an autonomous self-healing loop that queries the database and automatically 
backfills up to 45 days if tables are empty or the bot experienced downtime.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List

import db_manager

# Per-sport toggles so a flaky provider can be turned off without a redeploy.
ENABLE_MLB_STAT_INGEST = os.getenv("ENABLE_MLB_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NBA_STAT_INGEST = os.getenv("ENABLE_NBA_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_WNBA_STAT_INGEST = os.getenv("ENABLE_WNBA_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NFL_STAT_INGEST = os.getenv("ENABLE_NFL_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_SOCCER_STAT_INGEST = os.getenv("ENABLE_SOCCER_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_TENNIS_STAT_INGEST = os.getenv("ENABLE_TENNIS_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}

_DEFAULT_SOCCER_LEAGUES = "ENG-Premier League,USA-Major League Soccer"
SOCCER_STAT_LEAGUES = [
    league.strip()
    for league in (os.getenv("SOCCER_STAT_LEAGUES", "").strip() or _DEFAULT_SOCCER_LEAGUES).split(",")
    if league.strip()
]


def _yesterday() -> date:
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def _num(value: Any) -> Any:
    """Best-effort numeric coercion; returns None for blanks/non-numbers."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ip_to_outs(innings_pitched: Any) -> Any:
    """Convert MLB 'inningsPitched' (e.g. 6.2 = 6 ip + 2 outs) to total outs."""
    value = _num(innings_pitched)
    if value is None:
        return None
    whole = int(value)
    frac = round((value - whole) * 10)  # .0/.1/.2 -> 0/1/2 outs
    return whole * 3 + frac


def _parse_mlb_boxscore(box: Dict[str, Any], day: str) -> List[Dict[str, Any]]:
    """Parse a statsapi boxscore_data dict into per-player log rows with stat aliasing."""
    rows: List[Dict[str, Any]] = []
    home_team = str(((box.get("home") or {}).get("team") or {}).get("abbreviation") or "")
    away_team = str(((box.get("away") or {}).get("team") or {}).get("abbreviation") or "")
    for side in ("home", "away"):
        team_block = box.get(side) or {}
        players = team_block.get("players") or {}
        team_abbr = str(((team_block.get("team") or {}).get("abbreviation")) or "")
        opponent_abbr = away_team if side == "home" else home_team
        for player in players.values():
            name = str(((player.get("person") or {}).get("fullName")) or "").strip()
            if not name:
                continue
            stats = player.get("stats") or {}
            batting = stats.get("batting") or {}
            pitching = stats.get("pitching") or {}

            has_batting = bool(batting)
            has_pitching = bool(pitching)
            if not (has_batting or has_pitching):
                continue

            # Batting metrics
            hits = _num(batting.get("hits"))
            doubles = _num(batting.get("doubles")) or 0
            triples = _num(batting.get("triples")) or 0
            hrs = _num(batting.get("homeRuns"))
            runs = _num(batting.get("runs"))
            rbis = _num(batting.get("rbi"))
            walks = _num(batting.get("baseOnBalls"))
            sb = _num(batting.get("stolenBases"))
            k_bat = _num(batting.get("strikeOuts"))

            total_bases = None
            if hits is not None:
                total_bases = hits + doubles + 2 * triples + 3 * (hrs or 0)

            # Pitching metrics
            k_pitch = _num(pitching.get("strikeOuts"))
            outs = _ip_to_outs(pitching.get("inningsPitched"))
            hits_allowed = _num(pitching.get("hits"))
            earned_runs = _num(pitching.get("earnedRuns"))
            walks_allowed = _num(pitching.get("baseOnBalls"))

            # Determine primary strikeouts (pitching strikeouts take precedence for pitchers)
            strikeouts = k_pitch if has_pitching else k_bat

            rows.append({
                "game_date": day,
                "league": "baseball_mlb",
                "player_name": name,
                "team": team_abbr,
                "opponent": opponent_abbr,
                "hits": hits,
                "total_bases": total_bases,
                "home_runs": hrs,
                "rbis": rbis,
                "runs": runs,
                "walks": walks,
                "stolen_bases": sb,
                "strikeouts": strikeouts,
                "outs": outs,
                "hits_allowed": hits_allowed,
                "earned_runs": earned_runs,
                "walks_allowed": walks_allowed,
                "stats": {
                    "hits": hits,
                    "total_bases": total_bases,
                    "home_runs": hrs,
                    "batter_home_runs": hrs,
                    "runs": runs,
                    "batter_runs": runs,
                    "batter_runs_scored": runs,
                    "rbis": rbis,
                    "batter_rbis": rbis,
                    "walks": walks,
                    "stolen_bases": sb,
                    "strikeouts": strikeouts,
                    "pitcher_strikeouts": k_pitch,
                    "batter_strikeouts": k_bat,
                    "outs": outs,
                    "pitcher_outs": outs,
                    "hits_allowed": hits_allowed,
                    "earned_runs": earned_runs,
                    "walks_allowed": walks_allowed,
                },
            })
    return rows


def fetch_mlb_logs(game_date: date) -> List[Dict[str, Any]]:
    """MLB batter+pitcher box scores via MLB-StatsAPI (statsapi)."""
    try:
        import statsapi
    except ImportError:
        print("[stat_ingest] MLB-StatsAPI (statsapi) not installed; skipping MLB")
        return []

    try:
        schedule = statsapi.schedule(date=game_date.strftime("%m/%d/%Y"))
    except Exception as exc:
        print(f"[stat_ingest] MLB schedule fetch failed: {exc}")
        return []

    day = game_date.isoformat()
    rows: List[Dict[str, Any]] = []
    for game in schedule or []:
        game_id = game.get("game_id")
        if not game_id:
            continue
        try:
            box = statsapi.boxscore_data(game_id)
        except Exception as exc:
            print(f"[stat_ingest] MLB boxscore {game_id} failed: {exc}")
            continue
        rows.extend(_parse_mlb_boxscore(box, day))
    return rows


_ESPN_BASKETBALL_PATHS = {
    "basketball_nba": "basketball/nba",
    "basketball_wnba": "basketball/wnba",
}


def _espn_made(value: Any) -> Any:
    """ESPN 'made-attempted' stat (e.g. '7-12') -> the 'made' count."""
    if value is None:
        return None
    text = str(value)
    if "-" in text:
        text = text.split("-", 1)[0]
    return _num(text)


def _parse_espn_basketball_summary(
    summary: Dict[str, Any], game_date: date, league: str
) -> List[Dict[str, Any]]:
    """Parse an ESPN game-summary boxscore into per-player log rows."""
    rows: List[Dict[str, Any]] = []
    boxscore = summary.get("boxscore") or {}
    team_blocks = boxscore.get("players") or []
    team_abbrs = [str((team_block.get("team") or {}).get("abbreviation") or "") for team_block in team_blocks]
    for team_block in team_blocks:
        team = team_block.get("team") or {}
        team_abbr = str(team.get("abbreviation") or "")
        opponent = next((abbr for abbr in team_abbrs if abbr and abbr != team_abbr), "")
        for stat_group in team_block.get("statistics") or []:
            keys = stat_group.get("keys") or stat_group.get("names") or []
            for athlete_entry in stat_group.get("athletes") or []:
                if athlete_entry.get("didNotPlay"):
                    continue
                values = athlete_entry.get("stats") or []
                if not values:
                    continue
                keyed = dict(zip(keys, values))
                athlete = athlete_entry.get("athlete") or {}
                points = _num(keyed.get("points"))
                rebounds = _num(keyed.get("rebounds"))
                assists = _num(keyed.get("assists"))
                threes = _espn_made(
                    keyed.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                )
                rows.append({
                    "game_date": game_date.isoformat(),
                    "league": league,
                    "player_id": str(athlete.get("id") or ""),
                    "player_name": str(athlete.get("displayName") or ""),
                    "team": team_abbr,
                    "opponent": opponent,
                    "points": points,
                    "rebounds": rebounds,
                    "assists": assists,
                    "threes_made": threes,
                    "steals": _num(keyed.get("steals")),
                    "blocks": _num(keyed.get("blocks")),
                    "turnovers": _num(keyed.get("turnovers")),
                    "minutes": _num(keyed.get("minutes")),
                    "stats": {
                        "points": points, "rebounds": rebounds, "assists": assists,
                        "threes_made": threes,
                    },
                })
    return rows


def fetch_nba_logs(game_date: date, league: str = "basketball_nba") -> List[Dict[str, Any]]:
    """NBA/WNBA player box scores via ESPN's free public API."""
    sport_path = _ESPN_BASKETBALL_PATHS.get(league)
    if not sport_path:
        print(f"[stat_ingest] no ESPN path for {league}; skipping")
        return []

    from services.http_client import get_json

    day = game_date.strftime("%Y%m%d")
    scoreboard_url = (
        f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/scoreboard?dates={day}"
    )
    try:
        scoreboard = get_json(scoreboard_url)
    except Exception as exc:
        print(f"[stat_ingest] {league} scoreboard fetch failed: {exc}")
        return []

    event_ids = [
        str(event.get("id"))
        for event in (scoreboard.get("events") or [])
        if event.get("id")
    ]
    if not event_ids:
        return []

    rows: List[Dict[str, Any]] = []
    for event_id in event_ids:
        summary_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/summary?event={event_id}"
        )
        try:
            summary = get_json(summary_url)
        except Exception as exc:
            print(f"[stat_ingest] {league} summary {event_id} fetch failed: {exc}")
            continue
        rows.extend(_parse_espn_basketball_summary(summary, game_date, league))
    return rows


def _parse_espn_football_summary(
    summary: Dict[str, Any], game_date: date
) -> List[Dict[str, Any]]:
    """Parse an ESPN NFL game-summary boxscore into per-player log rows."""
    merged: Dict[str, Dict[str, Any]] = {}
    boxscore = summary.get("boxscore") or {}
    team_blocks = boxscore.get("players") or []
    team_abbrs = [str((team_block.get("team") or {}).get("abbreviation") or "") for team_block in team_blocks]
    for team_block in team_blocks:
        team = team_block.get("team") or {}
        team_abbr = str(team.get("abbreviation") or "")
        opponent = next((abbr for abbr in team_abbrs if abbr and abbr != team_abbr), "")
        for stat_group in team_block.get("statistics") or []:
            keys = stat_group.get("keys") or stat_group.get("names") or []
            for athlete_entry in stat_group.get("athletes") or []:
                values = athlete_entry.get("stats") or []
                if not values:
                    continue
                athlete = athlete_entry.get("athlete") or {}
                athlete_id = str(athlete.get("id") or "")
                if not athlete_id:
                    continue
                record = merged.setdefault(
                    athlete_id,
                    {
                        "name": str(athlete.get("displayName") or ""),
                        "team": team_abbr,
                        "opponent": opponent,
                        "keyed": {},
                    },
                )
                record["keyed"].update(dict(zip(keys, values)))

    rows: List[Dict[str, Any]] = []
    for athlete_id, record in merged.items():
        keyed = record["keyed"]
        passing_yards = _num(keyed.get("passingYards"))
        rushing_yards = _num(keyed.get("rushingYards"))
        receiving_yards = _num(keyed.get("receivingYards"))
        receptions = _num(keyed.get("receptions"))
        rows.append({
            "game_date": game_date.isoformat(),
            "league": "americanfootball_nfl",
            "player_id": athlete_id,
            "player_name": record["name"],
            "team": record["team"],
            "opponent": record.get("opponent", ""),
            "passing_yards": passing_yards,
            "passing_tds": _num(keyed.get("passingTouchdowns")),
            "rushing_yards": rushing_yards,
            "rushing_tds": _num(keyed.get("rushingTouchdowns")),
            "receiving_yards": receiving_yards,
            "receptions": receptions,
            "receiving_tds": _num(keyed.get("receivingTouchdowns")),
            "stats": {
                "passing_yards": passing_yards,
                "rushing_yards": rushing_yards,
                "receiving_yards": receiving_yards,
                "receptions": receptions,
            },
        })
    return rows


def fetch_nfl_logs(game_date: date) -> List[Dict[str, Any]]:
    """NFL player box scores via ESPN's free public API."""
    from services.http_client import get_json

    day = game_date.strftime("%Y%m%d")
    scoreboard_url = (
        f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={day}"
    )
    try:
        scoreboard = get_json(scoreboard_url)
    except Exception as exc:
        print(f"[stat_ingest] NFL scoreboard fetch failed: {exc}")
        return []

    event_ids = [
        str(event.get("id"))
        for event in (scoreboard.get("events") or [])
        if event.get("id")
    ]
    if not event_ids:
        return []

    rows: List[Dict[str, Any]] = []
    for event_id in event_ids:
        summary_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={event_id}"
        )
        try:
            summary = get_json(summary_url)
        except Exception as exc:
            print(f"[stat_ingest] NFL summary {event_id} fetch failed: {exc}")
            continue
        rows.extend(_parse_espn_football_summary(summary, game_date))
    return rows


def fetch_soccer_logs(game_date: date) -> List[Dict[str, Any]]:
    """Soccer player lines via soccerdata (FBref)."""
    try:
        import soccerdata as sd
    except ImportError:
        print("[stat_ingest] soccerdata not installed; skipping soccer")
        return []

    rows: List[Dict[str, Any]] = []
    for league in SOCCER_STAT_LEAGUES:
        try:
            fbref = sd.FBref(leagues=league, seasons=game_date.year)
            frame = fbref.read_player_match_stats(stat_type="summary")
        except Exception as exc:
            print(f"[stat_ingest] soccer '{league}' fetch failed: {exc}")
            continue
        if frame is None or getattr(frame, "empty", True):
            continue
        frame = frame.reset_index()
        for _, row in frame.iterrows():
            row_date = str(row.get("date") or "")[:10]
            if row_date and row_date != game_date.isoformat():
                continue
            rows.append({
                "game_date": row_date or game_date.isoformat(),
                "league": f"soccer:{league}",
                "player_name": str(row.get("player") or ""),
                "team": str(row.get("team") or ""),
                "goals": _num(row.get("Gls")),
                "assists": _num(row.get("Ast")),
                "shots": _num(row.get("Sh")),
                "shots_on_target": _num(row.get("SoT")),
                "minutes": _num(row.get("Min")),
                "stats": {
                    "goals": _num(row.get("Gls")),
                    "shots_on_target": _num(row.get("SoT")),
                    "shots": _num(row.get("Sh")),
                },
            })
    return rows


def fetch_tennis_logs(game_date: date) -> List[Dict[str, Any]]:
    """ATP/WTA match logs from Sackmann CSVs."""
    import csv
    import io
    import requests

    rows: List[Dict[str, Any]] = []
    year = game_date.year
    sources = {
        "ATP": f"https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv",
        "WTA": f"https://raw.githubusercontent.com/JeffSackmann/tennis_wta/master/wta_matches_{year}.csv",
    }
    target = game_date.strftime("%Y%m%d")
    for tour, url in sources.items():
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as exc:
            print(f"[stat_ingest] tennis {tour} fetch failed: {exc}")
            continue
        reader = csv.DictReader(io.StringIO(resp.text))
        league = "tennis_atp" if tour == "ATP" else "tennis_wta"
        for match in reader:
            if str(match.get("tourney_date") or "") != target:
                continue
            for side, opp in (("winner", "loser"), ("loser", "winner")):
                name = match.get(f"{side}_name")
                if not name:
                    continue
                rows.append({
                    "game_date": game_date.isoformat(),
                    "league": league,
                    "tour": tour,
                    "player_name": str(name),
                    "opponent": str(match.get(f"{opp}_name") or ""),
                    "tournament": str(match.get("tourney_name") or ""),
                    "surface": str(match.get("surface") or ""),
                    "result": "win" if side == "winner" else "loss",
                    "aces": _num(match.get(f"{'w' if side == 'winner' else 'l'}_ace")),
                    "double_faults": _num(match.get(f"{'w' if side == 'winner' else 'l'}_df")),
                    "stats": {
                        "aces": _num(match.get(f"{'w' if side == 'winner' else 'l'}_ace")),
                        "double_faults": _num(match.get(f"{'w' if side == 'winner' else 'l'}_df")),
                    },
                })
    return rows


def ingest_all(game_date: date | None = None) -> Dict[str, int]:
    """Fetch + upsert player logs autonomously.
    
    Queries Supabase to find the most recent log. If empty, backfills 45 days.
    If the bot experienced downtime, automatically fetches exactly the missing days.
    """
    base_date = game_date or _yesterday()
    totals: Dict[str, int] = {}
    max_lookback = 1
    
    try:
        from db_manager import supabase
        if supabase:
            for enabled, table in [
                (ENABLE_MLB_STAT_INGEST, "mlb_player_logs"),
                (ENABLE_NBA_STAT_INGEST, "nba_player_logs"),
                (ENABLE_WNBA_STAT_INGEST, "wnba_player_logs"),
                (ENABLE_NFL_STAT_INGEST, "nfl_player_logs"),
                (ENABLE_SOCCER_STAT_INGEST, "soccer_player_logs"),
                (ENABLE_TENNIS_STAT_INGEST, "tennis_match_logs"),
            ]:
                if not enabled:
                    continue
                res = supabase.table(table).select("game_date").order("game_date", desc=True).limit(1).execute()
                if res.data and res.data[0].get("game_date"):
                    latest_str = res.data[0]["game_date"][:10]
                    latest = datetime.fromisoformat(latest_str).date()
                    diff = (datetime.now(timezone.utc).date() - latest).days
                    max_lookback = max(max_lookback, min(45, diff))
                else:
                    max_lookback = 45  # Empty table detected, trigger full backfill
    except Exception as e:
        print(f"[stat_ingest] Auto-lookback calculation failed, defaulting to 1: {e}")

    print(f"[stat_ingest] Auto-detected required lookback: {max_lookback} days")

    for i in range(max_lookback):
        current_date = base_date - timedelta(days=i)
        print(f"[stat_ingest] ingesting player logs for {current_date.isoformat()}")

        jobs: List[tuple[bool, str, Callable[[], List[Dict[str, Any]]]]] = [
            (ENABLE_MLB_STAT_INGEST, "mlb_player_logs", lambda d=current_date: fetch_mlb_logs(d)),
            (ENABLE_NBA_STAT_INGEST, "nba_player_logs", lambda d=current_date: fetch_nba_logs(d, "basketball_nba")),
            (ENABLE_WNBA_STAT_INGEST, "wnba_player_logs", lambda d=current_date: fetch_nba_logs(d, "basketball_wnba")),
            (ENABLE_NFL_STAT_INGEST, "nfl_player_logs", lambda d=current_date: fetch_nfl_logs(d)),
            (ENABLE_SOCCER_STAT_INGEST, "soccer_player_logs", lambda d=current_date: fetch_soccer_logs(d)),
            (ENABLE_TENNIS_STAT_INGEST, "tennis_match_logs", lambda d=current_date: fetch_tennis_logs(d)),
        ]

        for enabled, table, fetch in jobs:
            if not enabled:
                continue
            try:
                rows = [row for row in fetch() if row.get("player_name")]
            except Exception as exc:
                print(f"[stat_ingest] {table} fetch raised: {exc}")
                rows = []
            count = db_manager.upsert_player_logs(table, rows) if rows else 0
            totals[table] = totals.get(table, 0) + count
            if count > 0:
                print(f"[stat_ingest] {table} ({current_date.isoformat()}): {count} rows upserted")

    return totals


if __name__ == "__main__":
    ingest_all()
