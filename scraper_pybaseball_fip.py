import pandas as pd
import pybaseball
from datetime import datetime
from db_manager import save_tracker_state

STATE_KEY = "mlb_fip_cache"
CACHE_FILE = "fip_cache.json"

def scrape_fip():
    """
    Pulls actual FIP from FanGraphs via pybaseball, maps it to MLBAM IDs,
    and saves it to the bot's cloud state so the MLB model can use it.
    """
    print("Initializing pybaseball FIP scraper...")
    season = datetime.now().year
    
    try:
        # 1. Fetch current season pitching stats from FanGraphs
        # qual=0 ensures we get all pitchers, not just qualified starters
        stats = pybaseball.pitching_stats(season, qual=0)
        
        # 2. Fetch Chadwick Bureau registry to map FanGraphs IDs to MLBAM IDs
        chadwick = pybaseball.chadwick_register()
        
        # 3. Merge DataFrames on the FanGraphs ID
        merged = stats.merge(chadwick, left_on='IDfg', right_on='key_fangraphs', how='inner')
        
        # 4. Build a dictionary mapping MLBAM ID -> Actual FIP and ERA
        fip_cache = {}
        for _, row in merged.iterrows():
            mlbam_id = row.get('key_mlbam')
            fip = row.get('FIP')
            era = row.get('ERA')
            
            # Ensure data is valid before caching
            if pd.notna(mlbam_id) and pd.notna(fip):
                fip_cache[str(int(mlbam_id))] = {
                    "fip": float(fip),
                    "era": float(era) if pd.notna(era) else 9.99
                }
                
        # 5. Save to cloud cache using the Bee-Baked db_manager
        save_tracker_state(STATE_KEY, fip_cache, CACHE_FILE)
        print(f"Successfully scraped and cached Actual FIP for {len(fip_cache)} MLB pitchers.")
        
        return {"detail": "pybaseball fip scrape complete", "count": len(fip_cache), "label": "updates"}
        
    except Exception as exc:
        print(f"Error scraping FIP via pybaseball: {exc}")
        return {"detail": f"pybaseball scrape error: {exc}", "count": 0, "label": "updates"}

if __name__ == "__main__":
    scrape_fip()