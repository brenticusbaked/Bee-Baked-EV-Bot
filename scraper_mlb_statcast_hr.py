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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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

def _safe_float(val, default=0.0):
    """Safely parses float values from MLB Stats API strings like '.250'."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def fetch_batter_power_stats(season=2026):
    """Pulls league-wide hitting stats (HR, Slugging, ISO) from the official MLB Stats API."""
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
            slg = _safe_float(stat.get("slg", stat.get("sluggingPercentage", 0.0)))
            avg = _safe_float(stat.get("avg", stat.get("battingAverage", 0.0)))
            iso = slg - avg
            
            if ab >= 30:  # Minimum sample size threshold
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

def calculate_hr_units(batter_stats, base_unit_size=3.0, kelly_fraction=0.25):
    """
    Evaluates cached batters and computes recommended unit sizes using 
    a Quarter-Kelly formula based on ISO and HR/AB rate thresholds.
    """
    recommendations = []
    for player_id, stats in batter_stats.items():
        iso = stats.get("iso", 0.0)
        hr_per_ab = stats.get("hr_per_ab", 0.0)
        
        # Tier 1 Elite Power Match (ISO >= .210 or HR/AB >= 0.048)
        # Tier 2 Strong Power Match (ISO >= .180 and HR/AB >= 0.038)
        is_tier_1 = (iso >= 0.210 or hr_per_ab >= 0.048)
        is_tier_2 = (iso >= 0.180 and hr_per_ab >= 0.038)
        
        if is_tier_1 or is_tier_2:
            # Model estimated probability based on power rate
            # Multiply by 4.2 to simulate average Plate Appearances per game
            implied_prob = min(0.35, max(0.12, hr_per_ab * 4.2))
            decimal_odds = 4.00  # Baseline prop odds reference (~+300)
            
            # Kelly Criterion: f* = (bp - q) / b
            b = decimal_odds - 1.0
            q = 1.0 - implied_prob
            kelly_pct = (b * implied_prob - q) / b
            
            if kelly_pct > 0:
                tier_multiplier = 1.0 if is_tier_1 else 0.65
                raw_units = round(kelly_pct * kelly_fraction * 100 * tier_multiplier, 2)
                final_units = max(0.5, round(raw_units * (base_unit_size / 3.0), 2))
                
                recommendations.append({
                    "name": stats["name"],
                    "iso": round(iso, 3),
                    "hr_per_ab": round(hr_per_ab, 3),
                    "tier": "Tier 1" if is_tier_1 else "Tier 2",
                    "recommended_units": final_units
                })
                
    # Sort recommendations by highest unit sizing
    recommendations.sort(key=lambda x: x["recommended_units"], reverse=True)
    return recommendations

def run_hr_pipeline():
    print("Initializing Free-Data Home Run Model Pipeline with Sizing...")
    slate = fetch_todays_mlb_slate()
    batter_stats = fetch_batter_power_stats()
    
    if not batter_stats:
        print("Loading fallback HR cache state...")
        batter_stats = load_tracker_state(STATE_KEY, {})
    else:
        save_tracker_state(STATE_KEY, batter_stats, CACHE_FILE)
        
    recommendations = calculate_hr_units(batter_stats)
    print(f"Generated unit sizing recommendations for {len(recommendations)} high-value power hitters.")
    
    return {
        "detail": "hr model execution complete",
        "count": len(batter_stats),
        "recommendations": recommendations,
        "label": "updates"
    }

if __name__ == "__main__":
    run_hr_pipeline()