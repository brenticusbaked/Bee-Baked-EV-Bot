import os

from db_manager import get_ungraded_past_bets, update_result
from services.bet_logic import grade_game_bet, normalize_text, parse_selection
from services.http_client import post_discord, request
from utils.odds import profit_for_result


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY")


def get_sgo_results(league_id, date_str):
    if not SGO_API_KEY:
        return {"players": {}, "events": []}

    url = "https://api.sportsgameodds.com/v2/events"
    params = {"apiKey": SGO_API_KEY, "leagueID": league_id, "date": date_str}
    try:
        response = request("GET", url, params=params, timeout=15)
        events = response.json()
        players = {}
        for event in events:
            for player_name, player_stats in event.get("boxscore", {}).items():
                players[normalize_text(player_name)] = player_stats
        return {"players": players, "events": events}
    except Exception as exc:
        print(f"SGO fetch failed for {league_id} on {date_str}: {exc}")
        return {"players": {}, "events": []}


def _match_event_by_name(events, matchup: str):
    matchup_norm = normalize_text(matchup)
    for event in events:
        event_name = normalize_text(event.get("name", ""))
        if matchup_norm and (matchup_norm == event_name or matchup_norm in event_name or event_name in matchup_norm):
            return event
    return None


def _grade_player_prop(bet, player_data):
    spec = parse_selection(bet["market"], bet["selection"])
    if spec.get("type") != "player_prop":
        return None

    stat = spec.get("stat")
    actual = player_data.get(stat, 0)
    line = spec.get("line")
    side = spec.get("side")
    if line is None or side not in {"over", "under"}:
        return None
    if actual == line:
        return "PUSH"
    if side == "over":
        return "WIN" if actual > line else "LOSS"
    return "WIN" if actual < line else "LOSS"


def run_grader():
    ungraded_bets = get_ungraded_past_bets()
    if not ungraded_bets:
        return {"detail": "no bets to grade", "count": 0, "label": "graded"}

    results_found = 0
    profit = 0.0
    cache = {}
    league_map = {"basketball_nba": "NBA", "icehockey_nhl": "NHL", "baseball_mlb": "MLB"}

    print(f"Grading {len(ungraded_bets)} bets...")

    for bet in ungraded_bets:
        league = league_map.get(bet.get("sport"))
        if not league:
            continue

        cache_key = f"{league}_{bet['date']}"
        if cache_key not in cache:
            cache[cache_key] = get_sgo_results(league, bet["date"])

        data = cache[cache_key]
        graded_result = None
        spec = parse_selection(bet["market"], bet["selection"])

        if spec.get("type") == "player_prop":
            player_key = normalize_text(spec.get("player", ""))
            player_data = data["players"].get(player_key)
            if player_data:
                graded_result = _grade_player_prop(bet, player_data)
        else:
            event = _match_event_by_name(data["events"], bet.get("matchup", ""))
            if event:
                graded_result = grade_game_bet(
                    bet["market"],
                    bet["selection"],
                    bet.get("matchup", ""),
                    event.get("scores", {}),
                )

        if not graded_result:
            continue

        update_result(bet["id"], graded_result)
        profit += profit_for_result(bet.get("odds", 0), bet.get("units", 0), graded_result)
        results_found += 1

    if results_found > 0:
        post_discord(
            {
                "embeds": [
                    {
                        "description": (
                            f"**SGO GRADER REPORT**\n"
                            f"Graded: {results_found}\n"
                            f"Net P/L: **{profit:+.2f} Units**"
                        ),
                        "color": 5763719 if profit >= 0 else 15158332,
                    }
                ]
            },
            webhook_url=DISCORD_WEBHOOK_URL,
        )

    return {"detail": "grading complete", "count": results_found, "label": "graded"}


if __name__ == "__main__":
    run_grader()
