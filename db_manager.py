import os
import json
from supabase import create_client, Client

# Supabase Configuration
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# --- SCRAPER FUNCTIONS ---

def save_odds_to_db(bookmaker: str, raw_json: dict):
    """Saves raw JSON from browser scrapers (DK, MGM, etc.)"""
    try:
        data = {
            "bookmaker": bookmaker,
            "payload": raw_json,
            "is_prop": False,
            "fetched_at": "now()"
        }
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"DB: {bookmaker.upper()} odds saved.")
    except Exception as e:
        print(f"DB ERROR saving {bookmaker}: {e}")

def save_props_to_db(bookmaker: str, raw_json: dict):
    """Saves raw player prop JSON (PrizePicks, etc.)"""
    try:
        data = {
            "bookmaker": bookmaker,
            "payload": raw_json,
            "is_prop": True,
            "fetched_at": "now()"
        }
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"DB: {bookmaker.upper()} props saved.")
    except Exception as e:
        print(f"DB ERROR saving {bookmaker} props: {e}")

# --- API CACHE FUNCTIONS (The missing pieces) ---

def save_master_cache(cache_data: dict):
    """
    Saves the aggregated Odds API data to the master_cache table.
    This resolves the ImportError in master_odds_fetcher.
    """
    try:
        # We store the entire dictionary as a single JSON blob for the models to read
        data = {
            "cache_json": cache_data,
            "updated_at": "now()"
        }
        # Using upsert on ID 1 ensures we only ever have one 'current' cache row
        supabase.table("master_cache").upsert({"id": 1, **data}).execute()
        print("DB: Master Cache updated successfully.")
    except Exception as e:
        print(f"DB ERROR saving master cache: {e}")

def get_master_cache():
    """Retrieves the latest master cache for the models."""
    try:
        response = supabase.table("master_cache").select("cache_json").eq("id", 1).single().execute()
        return response.data.get("cache_json", {})
    except Exception as e:
        print(f"DB ERROR retrieving cache: {e}")
        return {}
