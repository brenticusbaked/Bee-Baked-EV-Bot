import os
import requests
import csv
from datetime import datetime, timedelta

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
    'fanatics': 'https://sportsbook.fanatics.com/',
}

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, edge_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            # Updated to 10 columns
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle', 'Result'])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{edge_val:.2f}%", units, fair_price, "", ""
        ])

def get_best_spread(target_team):
    """Pulls the main line spread for the target team."""
    if not ODDS_API_KEY: return None, None, None, None
    
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    params = {
        'apiKey': ODDS_API_KEY, 
        'regions': 'us,eu', 
        'markets': 'spreads', # Upgraded to Spreads
        'bookmakers': 'fanduel,draftkings,betmgm,bet365,espn,fanatics',
        'oddsFormat': 'decimal'
    }
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200: return None, None, None, None
        
        best_price = 0.0
        best_point = ""
        best_book = "Unknown"
        best_book_title = "Unknown"
        
        for game in res.json():
            if target_team in game['home_team'] or target_team in game['away_team']:
                for bm in game.get('bookmakers', []):
                    for mkt in bm.get('markets', []):
                        if mkt['key'] == 'spreads':
                            for outcome in mkt['outcomes']:
                                if target_team in outcome['name']:
                                    price = float(outcome['price'])
                                    # Target standard main-line odds (usually between -115 and -105)
                                    if 1.85 <= price <= 1.96:
                                        if price > best_price:
                                            best_price = price
                                            best_point = outcome.get('point', '')
                                            best_book = bm['key']
                                            best_book_title = bm['title']
                                            
        if best_price > 0:
            link = BOOK_LINKS.get(best_book, "https://www.google.com/search?q=" + best_book + "+nba+odds")
            return best_book_title, to_american(best_price), f"{best_point}", link
    except Exception as e:
        print(f"Error fetching odds: {e}")
    return None, None, None, None

def get_espn_schedule(date_str):
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json().get('events', [])
    except Exception as e:
        print(f"Error fetching ESPN data: {e}")
    return []

def run_nba_model():
    today_obj = datetime.now()
    yesterday_obj = today_obj - timedelta(days=1)
    
    # SAFELY check for competitions existence
    teams_played_yesterday = [
        comp['team']['displayName']
        for game in get_espn_schedule(yesterday_obj.strftime("%Y%m%d"))
        if game.get('competitions') 
        for comp in game['competitions'][0].get('competitors', [])
    ]
            
    alerts = []
    for game in get_espn_schedule(today_obj.strftime("%Y%m%d")):
        # Skip if game has no competitions block (postponed/TBD)
        if not game.get('competitions'):
            continue
            
        comp = game['competitions'][0]
        away_team = next(c for c in comp['competitors'] if c['homeAway'] == 'away')['team']['displayName']
        home_team = next(c for c in comp['competitors'] if c['homeAway'] == 'home')['team']['displayName']
        matchup = f"{away_team} @ {home_team}"
        
        away_b2b = away_team in teams_played_yesterday
        home_b2b = home_team in teams_played_yesterday
        
        if away_b2b and not home_b2b:
            target_team, reason = home_team, f"{away_team} is on the 2nd night of a Back-to-Back."
        elif home_b2b and not away_b2b:
            target_team, reason = away_team, f"{home_team} is on the 2nd night of a Back-to-Back."
        else:
            continue 
            
        best_book, best_odds, spread_line, bet_link = get_best_spread(target_team)
        
        if best_book:
            odds_text = f"\n💰 **Best Odds:** {best_book} | **{spread_line} ({best_odds})**\n🔗 [Click here to bet]({bet_link})"
            log_bet_to_csv(matchup, "MODEL_NBA_SPREAD", f"{target_team} {spread_line}", best_odds, 5.0, "1.00", "MODEL")
        else:
            odds_text = "\n⚠️ *Main line spread not yet available.*"

        alerts.append(
            f"🏀 **NBA FATIGUE MODEL DETECTED** 🏀\n"
            f"**Game:** {matchup}\n━━━━━━━━━━━━━━━━━━━━\n"
            f"**Advantage:** {target_team}\n"
            f"📉 **The Edge:** {reason}\n{odds_text}"
        )
                        
    if DISCORD_WEBHOOK_URL:
        if alerts:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 16734003, "image": {"url": FOOTER_IMG}}]})
        else:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "🏀 **NBA Model Run:** No schedule mismatches today.", "color": 16734003}]})

if __name__ == "__main__":
    run_nba_model()