import os
import requests
import csv
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

BOOK_LINKS = {
    'draftkings': 'https://sportsbook.draftkings.com/',
    'fanduel': 'https://sportsbook.fanduel.com/',
    'betmgm': 'https://sports.betmgm.com/',
    'bet365': 'https://www.bet365.com/',
    'espn': 'https://espnbet.com/',
    'fanatics': 'https://sportsbook.fanatics.com/'
}

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, edge_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle'])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{edge_val:.2f}%", units, fair_price, ""
        ])

def get_best_f5_moneyline(target_team):
    """Pulls First 5 Innings (F5) Moneyline."""
    if not ODDS_API_KEY: return None, None, None
    
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {
        'apiKey': ODDS_API_KEY, 
        'regions': 'us,eu', 
        'markets': 'h2h_1st_half', # First 5 Innings Market!
        'bookmakers': 'fanduel,draftkings,betmgm,bet365,espn,fanatics',
        'oddsFormat': 'decimal'
    }
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200: return None, None, None
        
        best_price = 0.0
        best_book = "Unknown"
        best_book_title = "Unknown"
        
        for game in res.json():
            if target_team in game['home_team'] or target_team in game['away_team']:
                for bm in game.get('bookmakers', []):
                    for mkt in bm.get('markets', []):
                        if mkt['key'] == 'h2h_1st_half':
                            for outcome in mkt['outcomes']:
                                if target_team in outcome['name']:
                                    price = float(outcome['price'])
                                    if price > best_price:
                                        best_price = price
                                        best_book = bm['key']
                                        best_book_title = bm['title']
                                        
        if best_price > 0:
            link = BOOK_LINKS.get(best_book, "https://www.google.com/search?q=" + best_book + "+mlb+odds")
            return best_book_title, to_american(best_price), link
    except Exception as e:
        print(f"Error fetching odds: {e}")
    return None, None, None

def get_pitcher_stats(pitcher_id):
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
                            if innings > 20:
                                return era, whip
        except Exception:
            pass
    return None, None

def run_mlb_model():
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"
    alerts = []
    
    try:
        res = requests.get(url, timeout=15)
        if res.status_code != 200: return
            
        data = res.json()
        if not data.get('dates'): return
        
        for game in data['dates'][0].get('games', []):
            away_team = game['teams']['away']['team']['name']
            home_team = game['teams']['home']['team']['name']
            matchup = f"{away_team} @ {home_team}"
            
            away_p = game['teams']['away'].get('probablePitcher')
            home_p = game['teams']['home'].get('probablePitcher')
            
            if away_p and home_p:
                a_era, a_whip = get_pitcher_stats(away_p['id'])
                h_era, h_whip = get_pitcher_stats(home_p['id'])
                
                if a_era is not None and h_era is not None:
                    era_diff = abs(a_era - h_era)
                    whip_diff = abs(a_whip - h_whip)
                    
                    # Require BOTH a 1.50+ ERA gap and a 0.20+ WHIP gap
                    if era_diff >= 1.50 and whip_diff >= 0.20:
                        is_away_better = a_era < h_era
                        better_team = away_team if is_away_better else home_team
                        better_pitcher = away_p['fullName'] if is_away_better else home_p['fullName']
                        worse_pitcher = home_p['fullName'] if is_away_better else away_p['fullName']
                        
                        adv_era, adv_whip = min(a_era, h_era), min(a_whip, h_whip)
                        disadv_era, disadv_whip = max(a_era, h_era), max(a_whip, h_whip)
                        
                        best_book, best_odds, bet_link = get_best_f5_moneyline(better_team)
                        
                        odds_text = ""
                        if best_book:
                            odds_text = f"\n💰 **Best F5 Odds:** {best_book} @ **{best_odds}**\n🔗 [Click here to bet]({bet_link})"
                            log_bet_to_csv(matchup, "MODEL_MLB_F5", better_team, best_odds, era_diff, "1.00", "MODEL")
                        else:
                            odds_text = "\n⚠️ *F5 Odds not yet available.*"
                        
                        alerts.append(
                            f"⚾ **MLB MODEL MISMATCH DETECTED** ⚾\n"
                            f"**Game:** {matchup}\n━━━━━━━━━━━━━━━━━━━━\n"
                            f"**Advantage:** {better_team} ({better_pitcher})\n"
                            f"✅ {better_pitcher}: **{adv_era:.2f} ERA | {adv_whip:.2f} WHIP**\n"
                            f"❌ {worse_pitcher}: **{disadv_era:.2f} ERA | {disadv_whip:.2f} WHIP**\n"
                            f"{odds_text}"
                        )
                        
        if DISCORD_WEBHOOK_URL:
            if alerts:
                for msg in alerts: requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 3066993, "image": {"url": FOOTER_IMG}}]})
            else:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "⚾ **MLB Model:** No dual ERA/WHIP mismatches today.", "color": 3066993}]})
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_mlb_model()