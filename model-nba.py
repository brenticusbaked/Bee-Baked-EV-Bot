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
    'kalshi': 'https://kalshi.com/',
    'prizepicks': 'https://app.prizepicks.com/',
    'pinnacle': 'https://spankodds.com/' # Free live odds screen to view sharp movement
}

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, edge_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet'])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{edge_val:.2f}%", units, fair_price
        ])

def get_best_moneyline(target_team):
    if not ODDS_API_KEY: return None, None, None
    
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    params = {
        'apiKey': ODDS_API_KEY, 
        'regions': 'us,eu', 
        'markets': 'h2h', 
        # Added ESPN and Fanatics, removed Pinnacle!
        'bookmakers': 'fanduel,draftkings,betmgm,bet365,espn,fanatics',
        'oddsFormat': 'decimal'
    }
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code != 200: return None, None, None
        
        best_price = 0.0
        best_book = "Unknown"
        best_book_title = "Unknown"
        
        data = res.json()
        for game in data:
            if target_team in game['home_team'] or target_team in game['away_team']:
                for bm in game.get('bookmakers', []):
                    book_key = bm['key']
                    book_title = bm['title']
                    for mkt in bm.get('markets', []):
                        if mkt['key'] == 'h2h':
                            for outcome in mkt['outcomes']:
                                if target_team in outcome['name']:
                                    price = float(outcome['price'])
                                    if price > best_price:
                                        best_price = price
                                        best_book = book_key
                                        best_book_title = book_title
                                        
        if best_price > 0:
            link = BOOK_LINKS.get(best_book, "https://www.google.com/search?q=" + best_book + "+nba+odds")
            return best_book_title, to_american(best_price), link
    except Exception as e:
        print(f"Error fetching odds: {e}")
    return None, None, None

def get_espn_schedule(date_str):
    """Fetches NBA games for a specific YYYYMMDD date."""
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
    
    today_str = today_obj.strftime("%Y%m%d")
    yesterday_str = yesterday_obj.strftime("%Y%m%d")
    
    # 1. Find out who played yesterday
    yesterday_games = get_espn_schedule(yesterday_str)
    teams_played_yesterday = []
    for game in yesterday_games:
        for competitor in game['competitions'][0]['competitors']:
            teams_played_yesterday.append(competitor['team']['displayName'])
            
    # 2. Look at today's games
    today_games = get_espn_schedule(today_str)
    alerts = []
    
    for game in today_games:
        comp = game['competitions'][0]
        away = next(c for c in comp['competitors'] if c['homeAway'] == 'away')
        home = next(c for c in comp['competitors'] if c['homeAway'] == 'home')
        
        away_team = away['team']['displayName']
        home_team = home['team']['displayName']
        matchup = f"{away_team} @ {home_team}"
        
        away_b2b = away_team in teams_played_yesterday
        home_b2b = home_team in teams_played_yesterday
        
        # 3. Find the Mismatch: One team is on a B2B, the other is rested.
        if away_b2b and not home_b2b:
            target_team = home_team
            faded_team = away_team
            reason = f"{away_team} is on the 2nd night of a Back-to-Back (Road Fatigue)."
        elif home_b2b and not away_b2b:
            target_team = away_team
            faded_team = home_team
            reason = f"{home_team} is on the 2nd night of a Back-to-Back."
        else:
            continue # No rest advantage
            
        # Go get the odds!
        best_book, best_odds, bet_link = get_best_moneyline(target_team)
        
        if best_book:
            odds_text = f"\n💰 **Best Odds:** {best_book} @ **{best_odds}**\n🔗 [Click here to bet on {best_book}]({bet_link})"
            # Log as a 1 Unit play, using an arbitrary "5.0" as the fatigue edge value
            log_bet_to_csv(matchup, "MODEL_NBA_REST", target_team, best_odds, 5.0, "1.00", "MODEL")
        else:
            odds_text = "\n⚠️ *Odds not yet available.*"

        alerts.append(
            f"🏀 **NBA FATIGUE MODEL DETECTED** 🏀\n"
            f"**Game:** {matchup}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"**Advantage:** {target_team}\n"
            f"📉 **The Edge:** {reason}\n"
            f"{odds_text}"
        )
                        
    if alerts and DISCORD_WEBHOOK_URL:
        for msg in alerts:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 16734003, "image": {"url": FOOTER_IMG}}]}) # Orange
    else:
        if DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "🏀 **NBA Model Run:** No Back-to-Back schedule mismatches today.", "color": 16734003}]})

if __name__ == "__main__":
    run_nba_model()