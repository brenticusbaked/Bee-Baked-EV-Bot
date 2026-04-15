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

def is_already_logged(matchup, market, selection):
    """Prevents the bot from spamming the same NBA mismatch all day."""
    if not os.path.exists('bets_log.csv'): return False
    today = datetime.now().strftime("%Y-%m-%d")
    with open('bets_log.csv', 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or not any(row): continue
            if len(row) > 3 and row[0] == today:
                if row[1] == matchup and row[2].upper() == market.upper() and row[3] == selection:
                    return True
    return False

def log_bet_to_csv(matchup, market, selection, odds, edge_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
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
    params = {'apiKey': ODDS_API_KEY, 'regions': 'us,eu', 'markets': 'spreads', 'bookmakers': 'fanduel,draftkings,betmgm,bet365,espn,fanatics', 'oddsFormat': 'decimal'}
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            best_price, best_point, best_book, best_book_title = 0.0, "", "Unknown", "Unknown"
            for game in res.json():
                if target_team in game['home_team'] or target_team in game['away_team']:
                    for bm in game.get('bookmakers', []):
                        for mkt in bm.get('markets', []):
                            if mkt['key'] == 'spreads':
                                for outcome in mkt['outcomes']:
                                    if target_team in outcome['name']:
                                        price = float(outcome['price'])
                                        # Target standard main-line odds (usually between -115 and -105)
                                        if 1.85 <= price <= 1.96 and price > best_price:
                                            best_price = price
                                            best_point = outcome.get('point', '')
                                            best_book = bm['key']
                                            best_book_title = bm['title']
                                            
            if best_price > 0:
                link = BOOK_LINKS.get(best_book, f"https://www.google.com/search?q={best_book}+nba+odds")
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
    
    # Map teams that played yesterday to whether they were home or away
    teams_yesterday = {}
    for game in get_espn_schedule(yesterday_obj.strftime("%Y%m%d")):
        if game.get('competitions'):
            for comp in game['competitions'][0].get('competitors', []):
                teams_yesterday[comp['team']['displayName']] = comp['homeAway']
            
    alerts = []
    for game in get_espn_schedule(today_obj.strftime("%Y%m%d")):
        if not game.get('competitions'): continue
            
        comp = game['competitions'][0]
        away_team = next(c for c in comp['competitors'] if c['homeAway'] == 'away')['team']['displayName']
        home_team = next(c for c in comp['competitors'] if c['homeAway'] == 'home')['team']['displayName']
        matchup = f"{away_team} @ {home_team}"
        
        # Determine Fatigue Severity
        # 0 = Rested, 1 = Home B2B, 2 = Road to Home B2B, 3 = Home to Road B2B, 4 = Road to Road B2B (Highest Fatigue)
        away_fatigue, home_fatigue = 0, 0
        
        if away_team in teams_yesterday:
            away_fatigue = 4 if teams_yesterday[away_team] == 'away' else 3
            
        if home_team in teams_yesterday:
            home_fatigue = 2 if teams_yesterday[home_team] == 'away' else 1
            
        # We only care about massive scheduling mismatches (Differential of 3 or more)
        # Usually means a fully rested Home team is hosting a team on a Road/Road B2B
        fatigue_diff = away_fatigue - home_fatigue
        
        target_team, reason, edge = None, "", 0.0
        
        if fatigue_diff >= 3:
            target_team = home_team
            reason = f"{away_team} is on a brutal Road Back-to-Back. {home_team} is rested."
            edge = float(fatigue_diff) # Use the differential as an artificial edge metric
            
        elif fatigue_diff <= -3:
            target_team = away_team
            reason = f"{home_team} is on a Home Back-to-Back after traveling. {away_team} is rested."
            edge = float(abs(fatigue_diff))

        if target_team:
            best_book, best_odds, spread_line, bet_link = get_best_spread(target_team)
            market = "MODEL_NBA_SPREAD"
            selection = f"{target_team} {spread_line}"
            
            # Anti-Spam Check
            if best_book and not is_already_logged(matchup, market, selection):
                log_bet_to_csv(matchup, market, selection, best_odds, edge, "1.00", "MODEL")
                
                odds_text = f"\n💰 **Best Odds:** {best_book} | **{spread_line} ({best_odds})**\n🔗 [Click here to bet]({bet_link})"
                alerts.append(
                    f"🏀 **NBA TRAVEL FATIGUE DETECTED** 🏀\n"
                    f"**Game:** {matchup}\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"**Advantage:** {target_team}\n"
                    f"📉 **The Edge:** {reason}\n{odds_text}"
                )
                        
    if DISCORD_WEBHOOK_URL:
        if alerts:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 16734003, "image": {"url": FOOTER_IMG}}]})
            print(f"Sent {len(alerts)} NBA Fatigue mismatch alerts.")
        else:
            print("NBA Model Run Complete: No extreme schedule mismatches today.")

if __name__ == "__main__":
    run_nba_model()