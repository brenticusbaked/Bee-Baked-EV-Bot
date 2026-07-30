from __future__ import annotations

import os

import db_manager


def _is_player_prop_market(market_type: str) -> bool:
    return str(market_type).strip().lower().startswith(("player_", "batter_", "pitcher_"))


def _stat_label(market_key: str) -> str:
    key = str(market_key or "").strip().lower()
    mapping = {
        "batter_hits": "Hits",
        "batter_rbis": "RBIs",
        "batter_rbi": "RBIs",
        "batter_runs": "Runs",
        "batter_runs_scored": "Runs",
        "batter_total_bases": "Total Bases",
        "batter_home_runs": "Home Runs",
        "pitcher_strikeouts": "Strikeouts",
        "pitcher_outs": "Outs Recorded",
        "player_points": "Points",
        "player_rebounds": "Rebounds",
        "player_assists": "Assists",
        "player_threes": "3-Pointers Made",
        "player_threes_made": "3-Pointers Made",
        "player_three_pointers_made": "3-Pointers Made",
        "player_passing_yards": "Passing Yards",
        "player_rushing_yards": "Rushing Yards",
        "player_receiving_yards": "Receiving Yards",
        "player_receptions": "Receptions",
        "player_goals": "Goals",
        "player_shots_on_goal": "Shots on Goal",
    }
    return mapping.get(key, key.replace("_", " ").title())


def build_last_ten_context_line(
    target_name: str,
    market_key: str,
    point: object,
    side: str,
    sport: str,
    enabled: bool | None = None,
) -> str:
    if enabled is None:
        enabled = os.getenv("ENABLE_L10_CONTEXT", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return ""

    try:
        line_value = float(point) if point not in (None, "") else 0.0
    except (TypeError, ValueError):
        line_value = 0.0

    result = db_manager.get_l10_hit_rate(target_name, market_key, line_value, sport)
    prefix = "\n**Last 10:** "
    if not result or not result.get("games"):
        return f"{prefix}unavailable."

    games = int(result["games"])
    cleared = int(result["over"]) if str(side).strip().lower() == "over" else int(result["under"])
    direction = "cleared" if str(side).strip().lower() == "over" else "stayed under"

    if _is_player_prop_market(market_key):
        stat_label = _stat_label(market_key)
        target_label = target_name
        body = (
            f"{target_label} has {direction} {line_value:g} {stat_label} "
            f"in {cleared}/{games} of their last {games} games."
        )
    else:
        target_label = target_name
        body = f"{target_label} has {direction} in {cleared}/{games} of their last {games} games."

    last_game = result.get("last_game")
    if last_game and last_game.get("game_date") is not None:
        g_date = last_game.get("game_date")
        g_val = last_game.get("value")
        body += f" Last played {g_date}: recorded {g_val:g}."

    return prefix + body
