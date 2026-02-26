import json
import os
import sys
import time
import asyncio
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')

if not ODDS_API_KEY or not DISCORD_WEBHOOK_URL:
    print("❌ Error: Missing API Key or Webhook URL.")
    sys.exit()

# --- AGGRESSIVE CONFIG ---
# Added NHL to capture today's massive 12-game slate
LEAGUES_TO_SCAN = ['basketball_nba', 'basketball_ncaab', 'icehockey_nhl']
HISTORY_FILE = 'sent_alerts.json'

BOOKMAKER_LINKS = {
    'draftkings': 'https://sportsbook.draftkings.com/event/',
    'fanduel': 'https://sportsbook.fanduel.com/sports/event/',
    'betmgm': 'https://sports.betmgm.com/en/sports/events/',
    'espnbet': 'https://espnbet.com/',
    'bet365': 'https://www.bet365.com/',
    'caesars': 'https://www.williamhill.com/us/',
    'novig': 'https://novig.us/'
}

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        if len(history) > 1000:
            history = dict(list(history.items())[-1000:])
        json.dump(history, f, indent=4)

def format_odds(odds): return f"+{odds}" if odds > 0 else str(odds)

def american_to_implied(odds):
    return (100 / (odds + 100)) if odds > 0 else (abs(odds) / (abs(odds) + 100))

def implied_to_american(prob):
    if prob <= 0 or prob >= 1: return None
    return int(-round((prob / (1 - prob)) * 100)) if prob > 0.5 else int(round(((1 - prob) / prob) * 100))

def calculate_edge_metrics(sharp_odds_target, sharp_odds_opponent, soft_odds_target):
    ip_target = american_to_implied(sharp_odds_target)
    ip_opponent = american_to_implied(sharp_odds_opponent)
    if (ip_target + ip_opponent) == 0: return 0, 0, 0
    true_prob_win = ip_target / (ip_target + ip_opponent)
    true_prob_lose = 1 - true_prob_win
    fair_odds = implied_to_american(true_prob_win)
    profit = soft_odds_target if soft_odds_target > 0 else 100 / (abs(soft_odds_target) / 100)
    ev_percentage = round((true_prob_win * profit) - (true_prob_lose * 100), 2)
    b = ((soft_odds_target / 100) + 1 if soft_odds_target > 0 else (100 / abs(soft_odds_target)) + 1) - 1
    full_kelly = (b * true_prob_win - true_prob_lose) / b if b > 0 else 0
    return ev_percentage, round((full_kelly / 4) * 100, 2), fair_odds

def analyze_game_for_ev(game, min_edge=0.5): # 🚀 LOWERED EDGE FLOOR TO 0.5%
    ev_plays = []
    matchup = f"{game.get('home_team')} vs {game.get('away_team')}"
    sport_name = game.get('sport_title', 'Sports')
    unix_time = int(datetime.fromisoformat(game.get('commence_time').replace('Z', '+00:00')).timestamp())

    bookmakers = game.get('bookmakers', [])
    pinnacle = next((b for b in bookmakers if b['key'] == 'pinnacle'), None)
    if not pinnacle: return ev_plays
        
    for book in [b for b in bookmakers if b['key'] != 'pinnacle']:
        for market in book.get('markets', []):
            pin_market = next((m for m in pinnacle.get('markets', []) if m['key'] == market['key']), None)
            if not pin_market: continue
            for soft_out in market['outcomes']:
                soft_odds = soft_out['price']
                if soft_odds > 250 or soft_odds < -500: continue
                
                pin_target = next((o for o in pin_market['outcomes'] if o['name'] == soft_out['name'] and o.get('point') == soft_out.get('point')), None)
                pin_opp = next((o for o in pin_market['outcomes'] if o['name'] != soft_out['name'] and o.get('point') == soft_out.get('point')), None)
                if not pin_target or not pin_opp: continue
                    
                edge, q_k, fair = calculate_edge_metrics(pin_target['price'], pin_opp['price'], soft_odds)
                if edge >= min_edge and q_k > 0:
                    bet_label = f"{soft_out.get('description', '')} {soft_out['name']} {soft_out.get('point', '')}".strip()
                    ev_plays.append({
                        "id": f"{game['id']}_{market['key']}_{bet_label}_{book['title']}_{soft_odds}",
                        "matchup": matchup, "unix_time": unix_time, "market": market['key'].replace('_', ' ').title(),
                        "bet": bet_label, "odds": soft_odds, "fair": format_odds(fair), "book": book['title'],
                        "ev": edge, "kelly": q_k, "sport": sport_name, "game_id": game['id'], "book_key": book['key']
                    })
    return ev_plays

async def main():
    print(f"🐝 Scanning NBA, NCAAB, and NHL...")
    sent_history = load_history()
    async with aiohttp.ClientSession() as session:
        for league in LEAGUES_TO_SCAN:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
            params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    for game in await resp.json():
                        for play in analyze_game_for_ev(game):
                            if play['id'] not in sent_history:
                                await send_discord_alert_async(session, play)
                                sent_history[play['id']] = {"odds": play['odds'], "sent_at": datetime.now(timezone.utc).isoformat()}
                                await asyncio.sleep(1)
        save_history(sent_history)

# (Keep your existing send_discord_alert_async function here)

if __name__ == "__main__":
    asyncio.run(main())