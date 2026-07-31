import os
import requests
import pandas as pd
from datetime import datetime
from db_manager import save_tracker_state, load_tracker_state

STATE_KEY = "mlb_hr_model_cache"
CACHE_FILE = "hr_cache.json"

def fetch_todays_mlb_slate():
    """Fetches today's games and starting rosters using the official MLB Stats API."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today_str}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    games_list = []
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        for date_item in data.get("dates", []):
            for game in date_item.get("games", []):
                game_pk = game.get("gamePk")
                status = game.get("status", {}).get("abstractGameState")
                teams = game.get("teams", {})
                home_team = teams.get("home", {}).get("team", {}).get("name")
                away_team = teams.get("away", {}).get("team", {}).get("name")
                
                games_list.append({
                    "game_pk": game_pk,
                    "status": status,
                    "home_team": home_team,
                    "away_team": away_team
                })
        print(f"Successfully fetched {len(games_list)} games for today's MLB slate.")
    except Exception as e:
        print(f"Failed to fetch MLB schedule: {e}")
        
    return games_list

def fetch_batter_power_stats(season=2026):
    """Pulls league-wide hitting stats (HR, slugging, ISO) from the official MLB Stats API."""
    url = "https://statsapi.mlb.com/api/v1/stats"
    params = {
        "stats": "season",
        "group": "hitting",
        "season": season,
        "sportId": 1,
        "limit": 2000
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    
    batter_cache = {}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        splits = data.get("stats", [{}])[0].get("splits", [])
        for split in splits:
            player = split.get("player", {})
            stat = split.get("stat", {})
            
            player_id = str(player.get("id"))
            hr = int(stat.get("homeRuns", 0))
            ab = int(stat.get("atBats", 0))
            slg = float(stat.get("sluggingPercentage", 0.0) if stat.get("sluggingPercentage") else 0.0)
            iso = float(stat.get("iso", slg - float(stat.get("battingAverage", 0.0))))
            
            if ab > 20:  # Minimum threshold filter
                batter_cache[player_id] = {
                    "name": player.get("fullName"),
                    "home_runs": hr,
                    "at_bats": ab,
                    "hr_per_ab": hr / ab if ab > 0 else 0.0,
                    "slg": slg,
                    "iso": iso
                }
        print(f"Successfully compiled power metrics for {len(batter_cache)} batters.")
    except Exception as e:
        print(f"Error fetching MLB batter stats: {e}")
        
    return batter_cache

def run_hr_pipeline():
    print("Initializing Free-Data Home Run Model Pipeline...")
    slate = fetch_todays_mlb_slate()
    batter_stats = fetch_batter_power_stats()
    
    if not batter_stats:
        print("Loading fallback HR cache state...")
        batter_stats = load_tracker_state(STATE_KEY, {})
    else:
        save_tracker_state(STATE_KEY, batter_stats, CACHE_FILE)
        
    print(f"HR Model Pipeline complete. Tracked slate games: {len(slate)}, Cached Batters: {len(batter_stats)}")
    return {"detail": "hr model execution complete", "count": len(batter_stats), "label": "updates"}

if __name__ == "__main__":
    run_hr_pipeline()