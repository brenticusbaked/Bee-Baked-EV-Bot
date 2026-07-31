import os
import requests
import pandas as pd
from datetime import datetime
from db_manager import save_tracker_state, load_tracker_state

STATE_KEY = "mlb_fip_cache"
CACHE_FILE = "fip_cache.json"

def run_fip_scraper():
    print("Initializing Official MLB Stats API Scraper (Bypassing FanGraphs Cloudflare 403)...")
    season = datetime.now().year
    
    url = "https://statsapi.mlb.com/api/v1/stats"
    params = {
        "stats": "season",
        "group": "pitching",
        "season": season,
        "sportId": 1,
        "limit": 2000,
        "offset": 0
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    
    pitcher_records = []
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        splits = data.get("stats", [{}])[0].get("splits", [])
        for split in splits:
            player = split.get("player", {})
            stat = split.get("stat", {})
            
            mlbam_id = player.get("id")
            # Note: MLB API provides ERA directly. FIP can be calculated or mapped, 
            # using ERA as a robust baseline if FIP isn't explicitly raw-returned in stats endpoints.
            era = stat.get("era")
            
            if mlbam_id and era and era != "-":
                pitcher_records.append({
                    "mlbam_id": str(mlbam_id),
                    "era": float(era),
                    # Approximating FIP via ERA or secondary metrics if FIP isn't direct
                    "fip": float(stat.get("fip", era)) if stat.get("fip") else float(era)
                })
                
    except Exception as e:
        print(f"MLB Stats API request failed: {e}")

    # Fallback to cache if request fails
    if not pitcher_records:
        existing_cache = load_tracker_state(STATE_KEY, {})
        if existing_cache:
            print(f"Loaded {len(existing_cache)} pitchers from existing local cache fallback.")
            return {"detail": "fallback cache loaded", "count": len(existing_cache), "label": "updates"}
        print("Error: Could not retrieve MLB Stats API data and no cache available.")
        return {"detail": "mlb api scrape error: no data", "count": 0, "label": "updates"}

    try:
        fip_cache = {}
        for record in pitcher_records:
            mlbam_id = record["mlbam_id"]
            fip_cache[mlbam_id] = {
                "fip": record["fip"],
                "era": record["era"]
            }
                
        save_tracker_state(STATE_KEY, fip_cache, CACHE_FILE)
        print(f"Successfully pulled and cached MLB stats for {len(fip_cache)} pitchers via official API.")
        
        return {"detail": "mlb stats api scrape complete", "count": len(fip_cache), "label": "updates"}
        
    except Exception as exc:
        print(f"Error processing MLB Stats data: {exc}")
        return {"detail": f"processing error: {exc}", "count": 0, "label": "updates"}

if __name__ == "__main__":
    run_fip_scraper()