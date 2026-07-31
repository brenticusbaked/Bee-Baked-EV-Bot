import os
import random
import pandas as pd
import pybaseball
import requests
from datetime import datetime
from db_manager import save_tracker_state, load_tracker_state

STATE_KEY = "mlb_fip_cache"
CACHE_FILE = "fip_cache.json"

def run_fip_scraper():
    print("Initializing Direct API FanGraphs Scraper...")
    season = datetime.now().year
    
    url = "https://www.fangraphs.com/api/leaders/major-league/data"
    params = {
        "age": "",
        "pos": "all",
        "stats": "pit",
        "lg": "all",
        "qual": "0",
        "season": str(season),
        "season1": str(season),
        "month": "0",
        "team": "0,ts",
        "pageitems": "2000000000",
        "pagenum": "1",
        "ind": "0",
        "rost": "0",
        "players": "",
        "type": "8"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.fangraphs.com/leaders/major-league?pos=all&stats=pit&lg=all&qual=0&type=8&season=2026&month=0",
        "Origin": "https://www.fangraphs.com",
    }
    
    fg_data = None
    try:
        session = requests.Session()
        response = session.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        json_response = response.json()
        if "data" in json_response:
            fg_data = json_response["data"]
    except Exception as e:
        print(f"Cloudflare/API block encountered ({e}). Loading fallback cache state...")

    # Fallback to existing tracker cache if live fetch is blocked or fails
    if not fg_data:
        existing_cache = load_tracker_state(STATE_KEY, {})
        if existing_cache:
            print(f"Successfully loaded {len(existing_cache)} pitchers from existing local FIP cache fallback.")
            return {"detail": "fip fallback cache loaded successfully", "count": len(existing_cache), "label": "updates"}
        
        print("Error: Could not retrieve FanGraphs API data and no local fallback cache available.")
        return {"detail": "fangraphs scrape error: no data", "count": 0, "label": "updates"}

    try:
        stats = pd.DataFrame(fg_data)
        chadwick = pybaseball.chadwick_register()
        
        stats['PlayerId'] = stats['PlayerId'].astype(str)
        chadwick['key_fangraphs'] = chadwick['key_fangraphs'].astype(str)
        
        merged = stats.merge(chadwick, left_on='PlayerId', right_on='key_fangraphs', how='inner')
        
        fip_cache = {}
        for _, row in merged.iterrows():
            mlbam_id = row.get('key_mlbam')
            fip = row.get('FIP')
            era = row.get('ERA')
            
            if pd.notna(mlbam_id) and pd.notna(fip):
                fip_cache[str(int(mlbam_id))] = {
                    "fip": float(fip),
                    "era": float(era) if pd.notna(era) else 9.99
                }
                
        save_tracker_state(STATE_KEY, fip_cache, CACHE_FILE)
        print(f"Successfully scraped and cached Actual FIP for {len(fip_cache)} MLB pitchers.")
        
        return {"detail": "direct api fip scrape complete", "count": len(fip_cache), "label": "updates"}
        
    except Exception as exc:
        print(f"Error processing FanGraphs data: {exc}")
        return {"detail": f"fangraphs processing error: {exc}", "count": 0, "label": "updates"}

if __name__ == "__main__":
    run_fip_scraper()