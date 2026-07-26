"""Contextual Stat Enrichment Engine — daily box-score ingestion.

Runs once daily (see .github/workflows/daily_stat_ingest.yml), fetches the
previous day's player box scores from free stat libraries, and upserts them into
the Supabase ``*_player_logs`` / ``tennis_match_logs`` tables via ``db_manager``.

This module is NEVER called during the live odds scan — it is a batch job. Each
sport fetcher imports its library lazily and fails soft: a missing library or a
provider hiccup skips that sport without aborting the others.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List

import db_manager

# Per-sport toggles so a flaky provider can be turned off without a redeploy.
ENABLE_MLB_STAT_INGEST = os.getenv("ENABLE_MLB_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NBA_STAT_INGEST = os.getenv("ENABLE_NBA_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_WNBA_STAT_INGEST = os.getenv("ENABLE_WNBA_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NFL_STAT_INGEST = os.getenv("ENABLE_NFL_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_SOCCER_STAT_INGEST = os.getenv("ENABLE_SOCCER_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_TENNIS_STAT_INGEST = os.getenv("ENABLE_TENNIS_STAT_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}

# Soccer leagues (soccerdata FBref names) to ingest. Kept small to bound cost.
SOCCER_STAT_LEAGUES = [
    league.strip()
    for league in os.getenv("SOCCER_STAT_LEAGUES", "ENG-Premier League,USA-Major League Soccer").split(",")
    if league.strip()
]


def _yesterday() -> date:
    return (datetime.utcnow() - timedelta(days=1)).date()


def _num(value: Any) -> Any:
    """Best-effort numeric coercion; returns None for blanks/non-numbers."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_mlb_logs(game_date: date) -> List[Dict[str, Any]]:
    """Yesterday's MLB batter+pitcher lines via pybaseball statcast/game logs."""
    try:
        import pybaseball  # noqa: F401
        from pybaseball import statcast
    except ImportError:
        print("[stat_ingest] pybaseball not installed; skipping MLB")
        return []

    day = game_date.isoformat()
    try:
        data = statcast(start_dt=day, end_dt=day)
    except Exception as exc:  # pragma: no cover - network/provider dependent
        print(f"[stat_ingest] MLB fetch failed: {exc}")
        return []
    if data is None or getattr(data, "empty", True):
        return []

    rows: List[Dict[str, Any]] = []
    # statcast is pitch-level; aggregate to per-batter events for total bases etc.
    # Detailed aggregation is provider-specific; we store the raw daily line in
    # ``stats`` and populate the columns we can derive so get_l10_hit_rate works.
    try:
        for name, group in data.groupby("player_name"):
            events = group.get("events")
            singles = doubles = triples = hrs = hits = 0
            if events is not None:
                counts = events.value_counts().to_dict()
                singles = int(counts.get("single", 0))
                doubles = int(counts.get("double", 0))
                triples = int(counts.get("triple", 0))
                hrs = int(counts.get("home_run", 0))
                hits = singles + doubles + triples + hrs
            total_bases = singles + 2 * doubles + 3 * triples + 4 * hrs
            rows.append({
                "game_date": day,
                "league": "baseball_mlb",
                "player_name": str(name),
                "hits": hits,
                "total_bases": total_bases,
                "home_runs": hrs,
                "stats": {"hits": hits, "total_bases": total_bases, "home_runs": hrs},
            })
    except Exception as exc:  # pragma: no cover - schema drift guard
        print(f"[stat_ingest] MLB aggregation failed: {exc}")
        return []
    return rows


def fetch_nba_logs(game_date: date, league: str = "basketball_nba") -> List[Dict[str, Any]]:
    """Yesterday's NBA/WNBA player lines via nba_api league game logs."""
    try:
        from nba_api.stats.endpoints import playergamelogs
    except ImportError:
        print(f"[stat_ingest] nba_api not installed; skipping {league}")
        return []

    season_type = "Regular Season"
    try:
        logs = playergamelogs.PlayerGameLogs(
            date_from_nullable=game_date.strftime("%m/%d/%Y"),
            date_to_nullable=game_date.strftime("%m/%d/%Y"),
            league_id_nullable="10" if league == "basketball_wnba" else "00",
            season_type_nullable=season_type,
        )
        frame = logs.get_data_frames()[0]
    except Exception as exc:  # pragma: no cover - network/provider dependent
        print(f"[stat_ingest] {league} fetch failed: {exc}")
        return []
    if frame is None or getattr(frame, "empty", True):
        return []

    rows: List[Dict[str, Any]] = []
    for _, row in frame.iterrows():
        points = _num(row.get("PTS"))
        rebounds = _num(row.get("REB"))
        assists = _num(row.get("AST"))
        rows.append({
            "game_date": game_date.isoformat(),
            "league": league,
            "player_id": str(row.get("PLAYER_ID") or ""),
            "player_name": str(row.get("PLAYER_NAME") or ""),
            "team": str(row.get("TEAM_ABBREVIATION") or ""),
            "points": points,
            "rebounds": rebounds,
            "assists": assists,
            "threes_made": _num(row.get("FG3M")),
            "steals": _num(row.get("STL")),
            "blocks": _num(row.get("BLK")),
            "turnovers": _num(row.get("TOV")),
            "minutes": _num(row.get("MIN")),
            "stats": {
                "points": points, "rebounds": rebounds, "assists": assists,
                "threes_made": _num(row.get("FG3M")),
            },
        })
    return rows


def fetch_nfl_logs(game_date: date) -> List[Dict[str, Any]]:
    """Yesterday's NFL player lines via nfl_data_py weekly data."""
    try:
        import nfl_data_py as nfl
    except ImportError:
        print("[stat_ingest] nfl_data_py not installed; skipping NFL")
        return []
    try:
        frame = nfl.import_weekly_data([game_date.year])
    except Exception as exc:  # pragma: no cover - network/provider dependent
        print(f"[stat_ingest] NFL fetch failed: {exc}")
        return []
    if frame is None or getattr(frame, "empty", True):
        return []

    rows: List[Dict[str, Any]] = []
    for _, row in frame.iterrows():
        rows.append({
            "game_date": game_date.isoformat(),
            "league": "americanfootball_nfl",
            "player_id": str(row.get("player_id") or ""),
            "player_name": str(row.get("player_display_name") or row.get("player_name") or ""),
            "team": str(row.get("recent_team") or ""),
            "passing_yards": _num(row.get("passing_yards")),
            "passing_tds": _num(row.get("passing_tds")),
            "rushing_yards": _num(row.get("rushing_yards")),
            "rushing_tds": _num(row.get("rushing_tds")),
            "receiving_yards": _num(row.get("receiving_yards")),
            "receptions": _num(row.get("receptions")),
            "receiving_tds": _num(row.get("receiving_tds")),
            "stats": {
                "passing_yards": _num(row.get("passing_yards")),
                "rushing_yards": _num(row.get("rushing_yards")),
                "receiving_yards": _num(row.get("receiving_yards")),
                "receptions": _num(row.get("receptions")),
            },
        })
    return rows


def fetch_soccer_logs(game_date: date) -> List[Dict[str, Any]]:
    """Recent soccer player lines via soccerdata (FBref). Bounded to configured leagues."""
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
        except Exception as exc:  # pragma: no cover - network/provider dependent
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
    """Recent ATP/WTA match logs from the public Sackmann match CSVs."""
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
        except Exception as exc:  # pragma: no cover - network dependent
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
    """Fetch + upsert yesterday's logs for every enabled sport.

    Returns a per-table count of rows upserted. Each sport is isolated so one
    provider failure can't abort the rest.
    """
    game_date = game_date or _yesterday()
    print(f"[stat_ingest] ingesting player logs for {game_date.isoformat()}")

    jobs: List[tuple[bool, str, Callable[[], List[Dict[str, Any]]]]] = [
        (ENABLE_MLB_STAT_INGEST, "mlb_player_logs", lambda: fetch_mlb_logs(game_date)),
        (ENABLE_NBA_STAT_INGEST, "nba_player_logs", lambda: fetch_nba_logs(game_date, "basketball_nba")),
        (ENABLE_WNBA_STAT_INGEST, "wnba_player_logs", lambda: fetch_nba_logs(game_date, "basketball_wnba")),
        (ENABLE_NFL_STAT_INGEST, "nfl_player_logs", lambda: fetch_nfl_logs(game_date)),
        (ENABLE_SOCCER_STAT_INGEST, "soccer_player_logs", lambda: fetch_soccer_logs(game_date)),
        (ENABLE_TENNIS_STAT_INGEST, "tennis_match_logs", lambda: fetch_tennis_logs(game_date)),
    ]

    results: Dict[str, int] = {}
    for enabled, table, fetch in jobs:
        if not enabled:
            continue
        try:
            rows = [row for row in fetch() if row.get("player_name")]
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[stat_ingest] {table} fetch raised: {exc}")
            rows = []
        count = db_manager.upsert_player_logs(table, rows) if rows else 0
        results[table] = count
        print(f"[stat_ingest] {table}: {count} rows upserted")
    return results
