import os
import requests
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def get_pitcher_stats(pitcher_id):
    """
    Fetches the pitcher's stats. 
    It checks 2026 stats first, but falls back to 2025 since it's currently March.
    """
    current_year = datetime.now().year
    
    for year in [current_year, current_year - 1]:
        url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}?hydrate=stats(group=[pitching],type=[season],season={year})"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if 'people' in data and data['people']:
                    person = data['people'][0]
                    if 'stats' in person and person['stats']:
                        splits = person['stats'][0].get('splits', [])
                        if splits:
                            stats = splits[0].get('stat', {})
                            era = float(stats.get('era', 9.99))
                            whip = float(stats.get('whip', 2.00))
                            innings = float(stats.get('inningsPitched', 0))
                            
                            # Only trust the stats if they pitched at least 20 innings
                            if innings > 20:
                                return era, whip
        except Exception as e:
            pass
            
    return None, None

def run_mlb_model():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"
    
    alerts = []
    
    try:
        res = requests.get(url, timeout=15)
        if res.status_code != 200:
            print("Failed to fetch MLB schedule.")
            return
            
        data = res.json()
        dates = data.get('dates', [])
        if not dates:
            print("No MLB games today.")
            return
            
        games = dates[0].get('games', [])
        
        for game in games:
            matchup = f"{game['teams']['away']['team']['name']} @ {game['teams']['home']['team']['name']}"
            
            # Extract probable pitchers
            away_pitcher = game['teams']['away'].get('probablePitcher')
            home_pitcher = game['teams']['home'].get('probablePitcher')
            
            if away_pitcher and home_pitcher:
                away_id = away_pitcher['id']
                away_name = away_pitcher['fullName']
                
                home_id = home_pitcher['id']
                home_name = home_pitcher['fullName']
                
                # Fetch their underlying stats
                a_era, a_whip = get_pitcher_stats(away_id)
                h_era, h_whip = get_pitcher_stats(home_id)
                
                if a_era is not None and h_era is not None:
                    # CALCULATE THE DIFFERENTIAL
                    era_diff = abs(a_era - h_era)
                    
                    # If one pitcher is significantly better (ERA difference > 1.50)
                    if era_diff >= 1.50:
                        better_team = "Away" if a_era < h_era else "Home"
                        better_pitcher = away_name if a_era < h_era else home_name
                        worse_pitcher = home_name if a_era < h_era else away_name
                        
                        adv_era = min(a_era, h_era)
                        disadv_era = max(a_era, h_era)
                        
                        alerts.append(
                            f"⚾ **MLB MODEL MISMATCH DETECTED** ⚾\n"
                            f"**Game:** {matchup}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"**Advantage:** {better_team} ({better_pitcher})\n"
                            f"✅ {better_pitcher} ERA: **{adv_era:.2f}**\n"
                            f"❌ {worse_pitcher} ERA: **{disadv_era:.2f}**\n"
                            f"**ERA Differential:** {era_diff:.2f}\n\n"
                            f"💡 *Action:* Check Moneyline or First 5 Innings odds for {better_pitcher}'s team."
                        )
                        
        # Send alerts to Discord
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 3066993, "image": {"url": FOOTER_IMG}}]}) # Deep blue
                print("Model Alert Sent.")
        else:
            if DISCORD_WEBHOOK_URL:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "⚾ **MLB Model Run:** No massive pitching mismatches found today.", "color": 3066993}]})
            print("No significant mismatches.")
            
    except Exception as e:
        print(f"Error running MLB model: {e}")

if __name__ == "__main__":
    run_mlb_model()