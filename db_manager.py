import os
import json
from supabase import create_client, Client

# --- Supabase Configuration ---
url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

# Graceful fallback if keys aren't loaded properly
if url and key:
    supabase: Client = create_client(url, key)
else:
    supabase = None
    print("WARNING: Supabase URL or Key missing. DB writes will be bypassed.")

# ==========================================
# 1. SCRAPER FUNCTIONS (For Browser Data)
# ==========================================
def save_odds_to_db(bookmaker: str, raw_json: dict):
    if not supabase: return
    try:
        data = {"bookmaker": bookmaker, "payload": raw_json, "is_prop": False, "fetched_at": "now()"}
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"DB: {bookmaker.upper()} odds saved.")
    except Exception as e:
        print(f"DB ERROR saving {bookmaker}: {e}")

def save_props_to_db(bookmaker: str, raw_json: dict):
    if not supabase: return
    try:
        data = {"bookmaker": bookmaker, "payload": raw_json, "is_prop": True, "fetched_at": "now()"}
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"DB: {bookmaker.upper()} props saved.")
    except Exception as e:
        print(f"DB ERROR saving {bookmaker} props: {e}")

# ==========================================
# 2. MASTER CACHE (For Odds API Data)
# ==========================================
def save_master_cache(cache_data: dict):
    if not supabase: return
    try:
        data = {"cache_json": cache_data, "updated_at": "now()"}
        supabase.table("master_cache").upsert({"id": 1, **data}).execute()
        print("DB: Master Cache updated successfully.")
    except Exception as e:
        print(f"DB ERROR saving master cache: {e}")

def get_master_cache():
    if not supabase: return {}
    try:
        response = supabase.table("master_cache").select("cache_json").eq("id", 1).single().execute()
        return response.data.get("cache_json", {})
    except Exception as e:
        print(f"DB ERROR retrieving cache: {e}")
        return {}

# ==========================================
# 3. HTTP TRACKER (Fixes your current ImportError)
# ==========================================
def load_tracker_state():
    """Loads API rate limit tracker state from a local file."""
    try:
        if os.path.exists("tracker_state.json"):
            with open("tracker_state.json", "r") as f:
                return json.load(f)
        return {}
    except Exception:
        return {}

def save_tracker_state(state: dict):
    """Saves API rate limit tracker state to a local file."""
    try:
        with open("tracker_state.json", "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"Error saving tracker state: {e}")

# ==========================================
# 4. BET LOGGING (Prevents future model errors)
# ==========================================
def is_already_logged(matchup: str, market: str, selection: str) -> bool:
    """Checks if a specific bet was already alerted today to prevent spam."""
    if not supabase: return False
    try:
        # Simple check against the bet_log table
        response = supabase.table("bet_log") \
            .select("id") \
            .eq("matchup", matchup) \
            .eq("market", market) \
            .eq("selection", selection) \
            .execute()
        return len(response.data) > 0
    except Exception:
        return False

def log_bet_to_db(matchup, market, selection, odds, edge, units, sharp_odds, sport, event_id, notes=""):
    """Logs a new +EV bet alert into the database."""
    if not supabase: return False
    try:
        data = {
            "matchup": matchup,
            "market": market,
            "selection": selection,
            "odds": odds,
            "edge": edge,
            "units": units,
            "sharp_odds": sharp_odds,
            "sport": sport,
            "event_id": event_id,
            "notes": notes,
            "placed_at": "now()"
        }
        supabase.table("bet_log").insert(data).execute()
        return True
    except Exception as e:
        print(f"DB ERROR logging bet: {e}")
        return False
