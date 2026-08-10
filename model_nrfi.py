"""First Inning Run (NRFI / YRFI) predictive model utilizing 
starting pitcher first-inning WHIP, strikeout rates, and top-of-the-order wOBA.
"""

import logging
from typing import Any, Dict, Optional

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from model_mlb import _team_matches, get_advanced_pitcher_stats
from services.alerts import send_discord_alert
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import get_json as _http_get_json
from services.odds_reference import format_pinnacle_reference
from services.last_ten import build_last_ten_context_line
from utils.links import sportsbook_search_link
from utils.model_pricing import fair_american_from_probability, model_edge_from_probability, model_units_from_probability
from utils.odds import decimal_to_american, quarter_kelly_units, american_to_decimal
from utils.prop_pricing import negative_binomial_pmf
from utils.thresholds import env_float
from utils.time import get_local_now

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
NRFI_MODEL_EDGE_THRESHOLD = env_float("NRFI_MODEL_EDGE_THRESHOLD", 0.025)
NRFI_YRFI_MODEL_EDGE_THRESHOLD = env_float("NRFI_YRFI_MODEL_EDGE_THRESHOLD", 0.025)
NRFI_MODEL_MAX_UNITS = env_float("NRFI_MODEL_MAX_UNITS", 1.0)
NRFI_FIRST_INNING_SCALE = env_float("NRFI_FIRST_INNING_SCALE", 1.05)
NRFI_VARIANCE_MULTIPLIER = env_float("NRFI_VARIANCE_MULTIPLIER", 1.35)
NRFI_LEAGUE_AVG_RPG = env_float("NRFI_LEAGUE_AVG_RPG", 4.5)


def get_dynamic_link(bookmaker, target_string):
    return sportsbook_search_link(bookmaker, target_string)


def get_best_nrfi_odds(away_team: str, home_team: str, target_selection: str):
    cache = get_master_cache()
    if not cache:
        return None, None, None, None

    for game in cache.get("baseball_mlb", []):
        if not _team_matches(away_team, game.get("away_team", "")):
            continue
        if not _team_matches(home_team, game.get("home_team", "")):
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker["key"] == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                if market["key"] != "runs_1st_inning":
                    continue
                for outcome in market["outcomes"]:
                    if target_selection.lower() in outcome["name"].lower():
                        return (
                            bookmaker["title"],
                            decimal_to_american(float(outcome["price"])),
                            get_dynamic_link(bookmaker["key"], f"NRFI {away_team} @ {home_team}"),
                            market["key"],
                        )
    return None, None, None, None


def get_team_offense_factor(team_id: int, cache: Dict[int, float], season: Optional[int] = None) -> float:
    """Fetch a team's runs-per-game relative to league average from the MLB Stats API."""
    if team_id in cache:
        return cache[team_id]
    season = season or int(get_local_now().year)
    url = (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats?"
        f"stats=season&group=hitting&gameType=R&season={season}"
    )
    try:
        data = _http_get_json(url, timeout=8)
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            cache[team_id] = 1.0
            return 1.0
        stat = splits[0].get("stat", {})
        runs = float(stat.get("runs", 0) or 0)
        games = float(stat.get("gamesPlayed", 1) or 1)
        rpg = runs / games
        factor = rpg / NRFI_LEAGUE_AVG_RPG if NRFI_LEAGUE_AVG_RPG > 0 else 1.0
        cache[team_id] = float(max(0.5, min(1.5, factor)))
        return cache[team_id]
    except Exception as exc:
        logger.warning(f"Team offense fetch failed for {team_id}: {exc}")
        cache[team_id] = 1.0
        return 1.0


def run_nrfi_model():
    today = get_local_now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"

    try:
        data = _http_get_json(url)
        dates = data.get("dates", [])
        if not dates:
            return {"detail": "no mlb games scheduled", "count": 0, "label": "alerts"}

        alerts = []
        pitcher_stats_cache = {}
        team_offense_cache = {}
        for game in dates[0].get("games", []):
            event_id = game.get("id") or game.get("gamePk")
            if not event_id:
                continue
            away_team = game.get("teams", {}).get("away", {}).get("team", {}).get("name")
            away_team_id = game.get("teams", {}).get("away", {}).get("team", {}).get("id")
            home_team = game.get("teams", {}).get("home", {}).get("team", {}).get("name")
            home_team_id = game.get("teams", {}).get("home", {}).get("team", {}).get("id")
            if not away_team or not home_team or not away_team_id or not home_team_id:
                continue
            matchup = f"{away_team} @ {home_team}"

            away_p = game["teams"]["away"].get("probablePitcher")
            home_p = game["teams"]["home"].get("probablePitcher")
            if not away_p or not home_p:
                continue
            if not away_p.get("id") or not home_p.get("id"):
                continue

            # Data-driven first-inning probability:
            #   - Top of 1st: home pitcher vs away offense.
            #   - Bottom of 1st: away pitcher vs home offense.
            #   - No runs in first inning = both halves produce zero runs.
            #   - Each half is modeled as a Negative-Binomial (Poisson when variance=1.0).
            away_est, away_act, _, _ = get_advanced_pitcher_stats(away_p["id"], pitcher_stats_cache, {})
            home_est, home_act, _, _ = get_advanced_pitcher_stats(home_p["id"], pitcher_stats_cache, {})
            if (away_est is None and away_act is None) or (home_est is None and home_act is None):
                continue
            away_xera = away_act if away_act is not None else away_est
            home_xera = home_act if home_act is not None else home_est
            if away_xera is None or home_xera is None:
                continue
            away_offense = get_team_offense_factor(away_team_id, team_offense_cache)
            home_offense = get_team_offense_factor(home_team_id, team_offense_cache)
            lambda_top = (home_xera / 9.0) * away_offense * NRFI_FIRST_INNING_SCALE
            lambda_bot = (away_xera / 9.0) * home_offense * NRFI_FIRST_INNING_SCALE
            prob_top_zero = negative_binomial_pmf(0, lambda_top, NRFI_VARIANCE_MULTIPLIER)
            prob_bot_zero = negative_binomial_pmf(0, lambda_bot, NRFI_VARIANCE_MULTIPLIER)
            prob_no_runs = prob_top_zero * prob_bot_zero

            target_sides = [
                ("No", prob_no_runs, NRFI_MODEL_EDGE_THRESHOLD),
                ("Yes", 1.0 - prob_no_runs, NRFI_YRFI_MODEL_EDGE_THRESHOLD),
            ]

            for target_bet, target_prob, edge_threshold in target_sides:
                book, odds, link, selected_market = get_best_nrfi_odds(away_team, home_team, target_bet)
                if not book:
                    continue

                if is_already_logged(matchup, "MODEL_NRFI", target_bet):
                    continue

                pinnacle_reference = format_pinnacle_reference(
                    get_master_cache() or {},
                    "baseball_mlb",
                    event_id,
                    "MODEL_NRFI",
                    target_bet,
                )

                fair_p = fair_american_from_probability(target_prob)
                edge = model_edge_from_probability(target_prob, odds)
                if edge < edge_threshold:
                    continue

                units = max(0.25, round(model_units_from_probability(target_prob, odds, cap=NRFI_MODEL_MAX_UNITS), 2))
                if units <= 0:
                    continue

                was_logged = log_bet_to_db(
                    matchup,
                    "MODEL_NRFI",
                    target_bet,
                    odds,
                    edge,
                    f"{units:.2f}",
                    fair_p,
                    "baseball_mlb",
                    event_id,
                    notes=(
                        f"book={book};market=runs_1st_inning;"
                        f"model=nrfi_pitcher_profile;edge={edge:.4f};target={target_bet}"
                    ),
                )
                if not was_logged:
                    continue

                side_label = "NRFI" if target_bet == "No" else "YRFI"
                alerts.append(
                    f"**MLB {side_label} MODEL ALERT**\n"
                    f"**Game:** {matchup}\n"
                    f"**Selection:** Run Scored in 1st Inning -> {target_bet} ({side_label})\n"
                    f"**Price:** [{book}]({link}) @ {odds}\n"
                    f"**Pinnacle:** {pinnacle_reference}\n"
                    f"**Model Edge:** {edge * 100:.2f}% | **Fair:** {fair_p}\n"
                    f"**Size:** {units:.2f} Units"
                    f"{build_last_ten_context_line(away_team, 'runs_1st_inning', '', 'under', 'baseball_mlb')}"
                )

        for index, message in enumerate(alerts):
            send_discord_alert({"embeds": [{"description": message, "color": 3066993}]}, "model_nrfi", "bet_alert", message[:200], DISCORD_WEBHOOK_URL, index == len(alerts)-1)
        return {"detail": "nrfi model complete", "count": len(alerts), "label": "alerts"}
    except Exception as exc:
        return {"detail": f"error: {exc}", "count": 0, "label": "alerts"}

if __name__ == "__main__":
    run_nrfi_model()
