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
        # The HTTP_PROXY and HTTPS_PROXY environment variables we set in main.yml 
        # will automatically route this through your Webshare proxy to bypass the 403 error.
        stats = pybaseball.pitching_stats(2026, qual=0)
        
        # We only need specific columns for EV modeling to keep the database light
        target_columns = ['Name', 'Team', 'FIP', 'ERA', 'IP']
        
        # Ensure the columns actually exist in the fetched data
        if set(target_columns).issubset(stats.columns):
            # Convert the Pandas DataFrame to a list of dictionaries (JSON friendly)
            clean_data = stats[target_columns].dropna().to_dict(orient='records')
            
            # Save it to the database under the 'fangraphs_fip' bookmaker label
            save_odds_to_db("fangraphs_fip", {"pitchers": clean_data})
            print("✅ FanGraphs FIP data successfully fetched and cached.")
        else:
            print("❌ FanGraphs Scraper: Expected columns (like FIP) were missing.")

    except Exception as e:
        print(f"❌ pybaseball scrape error: {e}")

if __name__ == "__main__":
    # Allows you to test this file locally/independently
    run_fip_scraper()
