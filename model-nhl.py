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
    url = "https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': 'us,eu', 'markets': 'h2h', 'bookmakers': 'fanduel,draftkings,betmgm,bet365,pinnacle', 'oddsFormat': 'decimal'}
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
                    for mkt in bm.get('markets', []):
                        if mkt['key'] == 'h2h':
                            for outcome in mkt['outcomes']:
                                if target_team in outcome['name']:
                                    price = float(outcome['price'])
                                    if price > best_price:
                                        best_price = price
                                        best_book = bm['key']
                                        best_book_title = bm['title']
        if best_price > 0:
            link = BOOK_LINKS.get(best_book, "https://www.google.com/search?q=" + best_book + "+nhl+odds")
            return best_book_title, to_american(best_price), link
    except Exception as e:
        print(f"Error fetching odds: {e}")
    return None, None, None

def run_nhl_model():
    # 1. Grab current NHL Standings for Goal Differentials
    try:
        standings_res = requests.get("https://api-web.nhle.com/v1/standings/now", timeout=10)
        standings_data = standings_res.json()
        team_gd = {} # Dictionary mapping team names to their Goal Differential
        for team in standings_data.get('standings', []):
            name = team['teamName']['default']
            gd = team['goalDifferential']
            team_gd[name] = gd
            
        # 2. Get today's games
        today = datetime.now().strftime("%Y-%m-%d")
        sched_res = requests.get(f"https://api-web.nhle.com/v1/schedule/{today}", timeout=10)
        sched_data = sched_res.json()
        
        alerts = []
        if 'gameWeek' in sched_data and len(sched_data['gameWeek']) > 0:
            games = sched_data['gameWeek'][0].get('games', [])
            for game in games:
                away = game['awayTeam']['placeName']['default']
                home = game['homeTeam']['placeName']['default']
                matchup = f"{away} @ {home}"
                
                # Try to fuzzy match the team names (NHL API has weird naming conventions sometimes)
                away_gd = next((gd for name, gd in team_gd.items() if away in name), None)
                home_gd = next((gd for name, gd in team_gd.items() if home in name), None)
                
                if away_gd is not None and home_gd is not None:
                    gd_diff = abs(away_gd - home_gd)
                    
                    # If the goal differential gap is massive (> 40 goals)
                    if gd_diff >= 40:
                        better_team = away if away_gd > home_gd else home
                        better_gd = max(away_gd, home_gd)
                        worse_team = home if away_gd > home_gd else away
                        worse_gd = min(away_gd, home_gd)
                        
                        best_book, best_odds, bet_link = get_best_moneyline(better_team)
                        odds_text = ""
                        if best_book:
                            odds_text = f"\n💰 **Best Odds:** {best_book} @ **{best_odds}**\n🔗 [Click here to bet]({bet_link})"
                            # Log as a 1 Unit play, using the GD difference as the edge indicator
                            log_bet_to_csv(matchup, "MODEL_NHL", better_team, best_odds, gd_diff, "1.00", "MODEL")
                        else:
                            odds_text = "\n⚠️ *Odds not yet available.*"

                        alerts.append(
                            f"🏒 **NHL MODEL MISMATCH DETECTED** 🏒\n"
                            f"**Game:** {matchup}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"**Advantage:** {better_team}\n"
                            f"✅ {better_team} GD: **{better_gd}**\n"
                            f"❌ {worse_team} GD: **{worse_gd}**\n"
                            f"**Net Gap:** {gd_diff} Goals\n"
                            f"{odds_text}"
                        )
                        
        if alerts and DISCORD_WEBHOOK_URL:
            for msg in alerts:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 3447003, "image": {"url": FOOTER_IMG}}]}) # Blue
        else:
            if DISCORD_WEBHOOK_URL:
                requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "🏒 **NHL Model Run:** No major Goal Differential mismatches today.", "color": 3447003}]})
                
    except Exception as e:
        print(f"Error running NHL model: {e}")

if __name__ == "__main__":
    run_nhl_model()