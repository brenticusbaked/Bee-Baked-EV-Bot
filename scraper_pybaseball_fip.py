import os
import pybaseball
from db_manager import save_odds_to_db

def run_fip_scraper():
    """
    Fetches pitcher FIP and ERA data from FanGraphs using pybaseball.
    Saves the data to Supabase for the MLB model to use.
    """
    print("Starting FanGraphs/pybaseball FIP Scraper...")
    
    try:
        # Fetch pitching stats for the current 2026 season. 
        stats = pybaseball.pitching_stats(2026, qual=0)
        
        # Target specific columns for the EV model
        target_columns = ['Name', 'Team', 'FIP', 'ERA', 'IP']
        
        if set(target_columns).issubset(stats.columns):
            # Convert to a JSON-friendly format
            clean_data = stats[target_columns].dropna().to_dict(orient='records')
            
            # Save it directly to the database
            save_odds_to_db("fangraphs_fip", {"pitchers": clean_data})
            print("✅ FanGraphs FIP data successfully fetched and cached.")
        else:
            print("❌ FanGraphs Scraper: Expected columns (like FIP) were missing.")

    except Exception as e:
        print(f"❌ pybaseball scrape error: {e}")

if __name__ == "__main__":
    run_fip_scraper()
