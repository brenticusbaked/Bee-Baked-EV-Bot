import os
import json
from supabase import create_client, Client

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def save_odds_to_db(bookmaker: str, raw_json: dict):
    """
    Receives raw JSON from scrapers and stores it in the 'raw_scraper_data' table.
    """
    try:
        data = {
            "bookmaker": bookmaker,
            "payload": raw_json, # Stores the full JSON blob
            "fetched_at": "now()"
        }
        # Insert into your Supabase table
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"Successfully cached {bookmaker} data to Supabase.")
    except Exception as e:
        print(f"Error saving {bookmaker} to DB: {e}")

def save_props_to_db(bookmaker: str, raw_json: dict):
    """
    Specific handler for player prop JSON (PrizePicks/Underdog).
    """
    try:
        data = {
            "bookmaker": bookmaker,
            "payload": raw_json,
            "is_prop": True,
            "fetched_at": "now()"
        }
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"Successfully cached {bookmaker} props to Supabase.")
    except Exception as e:
        print(f"Error saving {bookmaker} props to DB: {e}")
