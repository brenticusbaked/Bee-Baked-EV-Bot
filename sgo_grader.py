import os
import time

import requests

from db_manager import get_ungraded_past_bets, update_result
from services.bet_logic import grade_game_bet, normalize_text, parse_selection
from services.discord_channels import RESULTS_WEBHOOK_URL
from services.http_client import post_discord, request
from utils.odds import profit_for_result


DISCORD_WEBHOOK_URL = RESULTS_WEBHOOK_URL
SGO_API_KEY = os.getenv("SGO_API_KEY")
SGO_API_KEY_2 = os.getenv("SGO_API_KEY_2")
SGO_API_KEY_3 = os.getenv("SGO_API_KEY_3")
SGO_GRADER_MAX_FETCHES = int(os.getenv("SGO_GRADER_MAX_FETCHES", "4"))
SGO_GRADER_FETCH_DELAY_SECONDS = float(os.getenv("SGO_GRADER_FETCH_DELAY_SECONDS", "1.25"))


def _sgo_keys():
    return [key for key in (SGO_API_KEY, SGO_API_KEY_2, SGO_API_KEY_3) if key]


def get_sgo_results(league_id, date_str, api_key):
    if not api_key:
        return {"players": {}, "events": [], "rate_limited": False, "unsupported": False}

    url = "https://api.sportsgameodds.com/v2/events"
    params = {"apiKey": api_key, "leagueID": league_id, "date": date_str}
    try:
        response = request("GET", url, params=params, timeout=15, retry_on_429=False)
        data = response.json()

        if isinstance(data, dict) and data.get("success") is False:
            error = (data.get("error") or "").lower()
            if "rate limit" in error or "quota" in error or "too many" in error:
                print(f"SGO rate-limited for {league_id} on {date_str}.")
                return {"players": {}, "events": [], "rate_limited": True, "unsupported": False}
            if "unsupported" in error:
                return {"players": {}, "events": [], "rate_limited": False, "unsupported": True}

        if isinstance(data, list):
            events = data
        elif isinstance(data, dict):
            events = data.get("events")
            if not isinstance(events, list):
                events = data.get("data")
            if isinstance(events, dict):
                events = events.get("events") or events.get("data")
            if not isinstance(events, list):
                print(f"SGO API returned unexpected format for {league_id} on {date_str}: {data}")
                return {"players": {}, "events": [], "rate_limited": False, "unsupported": False}
        else:
            print(f"SGO API returned unexpected format for {league_id} on {date_str}: {data}")
            return {"players": {}, "events": [], "rate_limited": False, "unsupported": False}

        players = {}
        for event in events:
            for player_name, player_stats in event.get("boxscore", {}).items():
                players[normalize_text(player_name)] = player_stats
        return {"players": players, "events": events, "rate_limited": False, "unsupported": False}
    except requests.HTTPError as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        print(f"SGO fetch failed for {league_id} on {date_str}: {exc}")
        
        # Identify rate limits vs hard rejections (like unsupported leagues)
        if status_code == 429:
            return {"players": {}, "events": [], "rate_limited": True, "unsupported": False}
        if status_code == 400 or "unsupported" in str(exc).lower():
            return {"players": {}, "events": [], "rate_limited": False, "unsupported": True}
            
        return {"players": {}, "events": [], "rate_limited": False, "unsupported": False}
    except Exception as exc:
        print(f"SGO fetch failed for {league_id} on {date_str}: {exc}")
        return {"players": {}, "events": [], "rate_limited": False, "unsupported": False}


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
    sgo_keys = _sgo_keys()
    if not sgo_keys:
        print("No SGO API keys configured. Skipping grader.")
        return {"detail": "SGO_API_KEY missing", "count": 0, "label": "graded"}

    ungraded_bets = get_ungraded_past_bets()
    if not ungraded_bets:
        return {"detail": "no bets to grade", "count": 0, "label": "graded"}

    results_found = 0
    profit = 0.0
    cache = {}
    cache_fetches = 0
    hit_rate_limit = False
    unsupported_leagues = set() # Track leagues that throw a 400 error
    league_map = {"basketball_nba": "NBA", "icehockey_nhl": "NHL", "baseball_mlb": "MLB"}

    print(f"Grading {len(ungraded_bets)} bets...")

    for bet in ungraded_bets:
        league = league_map.get(bet.get("sport"))
        
        # Instantly skip if we already know this league is unsupported on your API plan
        if not league or league in unsupported_leagues:
            continue

        cache_key = f"{league}_{bet['date']}"
        if cache_key not in cache:
            if cache_fetches >= SGO_GRADER_MAX_FETCHES:
                continue
            
            result = {"players": {}, "events": [], "rate_limited": False, "unsupported": False}
            for key_index, api_key in enumerate(sgo_keys, start=1):
                result = get_sgo_results(league, bet["date"], api_key)
                
                if result.get("unsupported"):
                    print(f"League {league} is unsupported on this plan. Skipping for remainder of run.")
                    unsupported_leagues.add(league)
                    break # Break the key loop; don't waste backup keys on a 400 error
                    
                if not result.get("rate_limited"):
                    break # Success! Break the key loop
                    
                print(f"SGO rate-limited for {league} on key #{key_index}; trying next key.")
                
            if result.get("unsupported"):
                continue # Skip grading this bet
                
            cache[cache_key] = result
            cache_fetches += 1
            if cache[cache_key].get("rate_limited"):
                hit_rate_limit = True
                break
            time.sleep(SGO_GRADER_FETCH_DELAY_SECONDS)

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

    if hit_rate_limit:
        print("SGO grader stopped early after a rate-limit response.")

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

    return {
        "detail": (
            f"grading complete ({cache_fetches} SGO date fetches)"
            + (" | stopped on rate limit" if hit_rate_limit else "")
        ),
        "count": results_found,
        "label": "graded",
    }


if __name__ == "__main__":
    run_grader()