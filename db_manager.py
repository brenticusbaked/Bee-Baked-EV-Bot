import os
from supabase import create_client, Client

# Supabase Configuration
url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def save_odds_to_db(bookmaker: str, raw_json: dict):
    """
    Saves raw API/Scraper JSON to Supabase. 
    Resolves the ImportError in the scraper files.
    """
    try:
        data = {
            "bookmaker": bookmaker,
            "payload": raw_json,
            "is_prop": False,
            "fetched_at": "now()"
        }
        # Assuming you ran the SQL to create 'raw_scraper_data'
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"DB: {bookmaker.upper()} odds saved to Supabase.")
    except Exception as e:
        print(f"DB ERROR saving {bookmaker}: {e}")

def save_props_to_db(bookmaker: str, raw_json: dict):
    """
    Saves raw player prop JSON (like PrizePicks) to Supabase.
    """
    try:
        data = {
            "bookmaker": bookmaker,
            "payload": raw_json,
            "is_prop": True,
            "fetched_at": "now()"
        }
        supabase.table("raw_scraper_data").insert(data).execute()
        print(f"DB: {bookmaker.upper()} props saved to Supabase.")
    except Exception as e:
        print(f"DB ERROR saving {bookmaker} props: {e}")

# ... Keep your existing get_master_cache or save_master_cache functions below ...
