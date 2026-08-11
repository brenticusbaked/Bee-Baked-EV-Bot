from __future__ import annotations

import os

import db_manager

HOME_RUN_MARKETS = {"batter_home_runs", "batter_home_run", "home_runs", "home_run"}


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
        "player_pass_yds": "Passing Yards",
        "player_rushing_yards": "Rushing Yards",
        "player_receiving_yards": "Receiving Yards",
        "player_receiving_yds": "Receiving Yards",
        "player_reception_yds": "Receiving Yards",
        "player_receptions": "Receptions",
        "player_anytime_td": "Touchdowns",
        "player_goals": "Goals",
        "player_shots_on_goal": "Shots on Goal",
    }
    return mapping.get(key, key.replace("_", " ").title())


def normalize_side(side: object) -> str | None:
    """Map an outcome label onto ``over`` / ``under``, or ``None`` if it is neither.

    Callers pass whatever the feed called the outcome, which for game markets is
    a team name. Returning ``None`` there matters: guessing would silently report
    the *under* hit rate for a side that has nothing to do with a total.
    """
    text = str(side or "").strip().lower()
    if text in {"over", "o", "yes"}:
        return "over"
    if text in {"under", "u", "no"}:
        return "under"
    return None


def _describe_counts(result: dict, direction: str) -> tuple:
    games = int(result["games"])
    cleared = int(result["over"]) if direction == "over" else int(result["under"])
    push = int(result.get("push") or 0)
    push_text = f" ({push} push)" if push else ""
    return games, cleared, push_text


def _recent_form_suffix(result: dict, opponent: object, unit: str = "") -> str:
    """Trailing 'Last vs X' / 'Last played' detail, whichever is available."""
    suffix = f"{unit}" if unit else ""
    last_vs_game = result.get("last_vs_game")
    if opponent and last_vs_game and last_vs_game.get("game_date") is not None:
        # Name the team from the matched log row: the caller may have offered
        # several candidate opponents without knowing the player's side.
        vs_label = last_vs_game.get("opponent") or opponent
        return f" Last vs {vs_label}: {last_vs_game['value']:g}{suffix} on {last_vs_game['game_date']}."
    last_game = result.get("last_game")
    if last_game and last_game.get("game_date") is not None:
        return f" Last played {last_game['game_date']}: recorded {last_game['value']:g}{suffix}."
    return ""


def build_last_ten_context_line(
    target_name: str,
    market_key: str,
    point: object,
    side: str,
    sport: str,
    opponent: str | None = None,
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

    result = db_manager.get_l10_hit_rate(target_name, market_key, line_value, sport, opponent=opponent)
    prefix = "\n**Last 10:** "
    if not result or not result.get("games"):
        return f"{prefix}unavailable."

    market = str(market_key).strip().lower()
    direction = normalize_side(side)

    if direction is None:
        # Not an over/under outcome (a team side, a first-basket scorer, ...).
        # Report the raw form rather than inventing a direction.
        values = result.get("values") or []
        average = sum(values) / len(values) if values else 0.0
        body = (
            f"{target_name} averaging {average:.2f} {_stat_label(market)} "
            f"over their last {int(result['games'])} games."
        )
        return prefix + body + _recent_form_suffix(result, opponent)

    games, cleared, push_text = _describe_counts(result, direction)

    if market in HOME_RUN_MARKETS:
        if direction == "over":
            body = f"{target_name} has homered in {cleared}/{games} of their last {games} games."
        else:
            body = f"{target_name} has been held without a home run in {cleared}/{games} of their last {games} games."
        return prefix + body + _recent_form_suffix(result, opponent, unit=" HR")

    verb = "cleared" if direction == "over" else "stayed under"
    if _is_player_prop_market(market):
        body = (
            f"{target_name} has {verb} {line_value:g} {_stat_label(market)} "
            f"in {cleared}/{games} of their last {games} games{push_text}."
        )
    else:
        body = f"{target_name} has {verb} in {cleared}/{games} of their last {games} games{push_text}."

    return prefix + body + _recent_form_suffix(result, opponent)
