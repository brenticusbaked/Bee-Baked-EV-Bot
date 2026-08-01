import os
import time
import requests
from datetime import datetime, timezone
from difflib import SequenceMatcher
from supabase import create_client

SGO_API_KEY = os.environ.get("SGO_API_KEY")
SGO_API_KEY_2 = os.environ.get("SGO_API_KEY_2")
SGO_API_KEY_3 = os.environ.get("SGO_API_KEY_3")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SGO_LEAGUE_STAGGER_SECONDS = float(os.environ.get("SGO_LEAGUE_STAGGER_SECONDS", "1.5"))

TARGET_BOOKS = ["circa", "pinnacle", "draftkings"]
LEAGUE_MAP = {
    "MLB": "baseball_mlb",
    "WNBA": "basketball_wnba"
}

def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _sgo_keys():
    return [key for key in (SGO_API_KEY, SGO_API_KEY_2, SGO_API_KEY_3) if key]

def get_odds_api_schedule(sport_key):
    """Fetches upcoming events from The Odds API to use as the base IDs."""
    if not ODDS_API_KEY:
        print("Warning: ODDS_API_KEY is not set.")
        return []
        
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events?apiKey={ODDS_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        events = response.json()
        return [{"id": e.get("id"), "home": e.get("home_team", ""), "away": e.get("away_team", "")} for e in events]
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch Odds API schedule for {sport_key}: {e}")
        return []

def fetch_sgo_sharp_lines():
    sgo_keys = _sgo_keys()
    if not sgo_keys or not ODDS_API_KEY:
        print("Error: at least one SGO_API_KEY and ODDS_API_KEY environment variable are required.")
        return

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Error: SUPABASE_URL or SUPABASE_KEY environment variable is missing.")
        return

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    odds_rows = []
    
    league_items = list(LEAGUE_MAP.items())
    for league_index, (sgo_league, odds_api_sport) in enumerate(league_items):
        print(f"Fetching mapping and SGO sharp odds for {sgo_league}...")
        
        # 1. Build the ID Map for this league
        odds_events = get_odds_api_schedule(odds_api_sport)
        league_success = False

        for key_index, sgo_key in enumerate(sgo_keys, start=1):
            url = f"https://api.sportsgameodds.com/v2/events?apiKey={sgo_key}&leagueID={sgo_league}&date={today}&includeOdds=true"

            try:
                response = requests.get(url, timeout=20)
                if response.status_code == 429:
                    print(f"SGO rate limit reached (429) for {sgo_league} on key #{key_index}. Trying next key.")
                    continue
                response.raise_for_status()
                data = response.json()

                for event in data.get("events", []):
                    sgo_id = str(event.get("eventID"))
                    home_team = event.get("homeTeam", {}).get("name", "")
                    away_team = event.get("awayTeam", {}).get("name", "")

                    # Match SGO event to Odds API event
                    mapped_fixture_id = None
                    best_score = 0
                    for o_event in odds_events:
                        score = (similar(home_team, o_event["home"]) + similar(away_team, o_event["away"])) / 2
                        if score > best_score:
                            best_score = score
                            mapped_fixture_id = o_event["id"]

                    # Skip if no confident match (>70% similarity)
                    if not mapped_fixture_id or best_score < 0.7:
                        continue

                    # 2. Extract targeted sharp odds
                    for odd in event.get("odds", []):
                        bookmaker = str(odd.get("sportsbook", "")).lower()

                        if bookmaker in TARGET_BOOKS:
                            market_key = odd.get("marketType", "unknown")
                            outcome_name = odd.get("selection", "unknown")
                            price = odd.get("decimalPrice")
                            point = odd.get("line")
                            last_update = odd.get("timestamp", datetime.now(timezone.utc).isoformat())

                            line_hash = f"{mapped_fixture_id}|{bookmaker}|{market_key}|{outcome_name}|{point}|{price}|{last_update}"

                            odds_rows.append(
                                {
                                    "fixture_id": mapped_fixture_id,
                                    "sport_key": odds_api_sport,
                                    "bookmaker_key": bookmaker,
                                    "bookmaker_title": bookmaker.capitalize(),
                                    "market_key": market_key,
                                    "outcome_name": outcome_name,
                                    "outcome_description": None,
                                    "point": point,
                                    "price_decimal": price,
                                    "line_hash": line_hash,
                                    "last_update": last_update,
                                    "captured_at": datetime.now(timezone.utc).isoformat(),
                                    "raw_outcome": odd,
                                }
                            )
                league_success = True
                break

            except requests.exceptions.RequestException as e:
                print(f"Failed to fetch SGO data for {sgo_league} with key #{key_index}: {e}")

        if not league_success:
            print(f"No usable SGO response for {sgo_league} after trying {len(sgo_keys)} key(s).")

        if league_index < len(league_items) - 1 and SGO_LEAGUE_STAGGER_SECONDS > 0:
            time.sleep(SGO_LEAGUE_STAGGER_SECONDS)

    # 3. Upsert mapped data to Supabase
    if odds_rows:
        try:
            batch_size = 500
            for i in range(0, len(odds_rows), batch_size):
                batch = odds_rows[i:i+batch_size]
                supabase.table("historical_odds").upsert(
                    batch, on_conflict="line_hash", ignore_duplicates=True
                ).execute()
            print(f"Successfully injected {len(odds_rows)} mapped sharp lines from SGO into Supabase.")
        except Exception as e:
            print(f"Supabase upsert failed: {e}")
    else:
        print("No target sharp lines found or mapped for today's slate.")

if __name__ == "__main__":
    fetch_sgo_sharp_lines()
