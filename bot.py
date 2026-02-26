import json
import os
import sys
import time
import asyncio
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv

# --- SECURE CREDENTIAL LOADING ---
load_dotenv()

ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')

if not ODDS_API_KEY or not DISCORD_WEBHOOK_URL:
    print("❌ Error: Missing API Key or Webhook URL. Check your GitHub Secrets.")
    sys.exit()

# --- SMART CONFIGURATION ---
LEAGUES_TO_SCAN = ['basketball_nba', 'basketball_ncaab']
HISTORY_FILE = 'sent_alerts.json'

BOOKMAKER_LINKS = {
    'draftkings': 'https://sportsbook.draftkings.com/event/',
    'fanduel': 'https://sportsbook.fanduel.com/sports/event/',
    'betmgm': 'https://sports.betmgm.com/en/sports/events/',
    'espnbet': 'https://espnbet.com/',
    'bet365': 'https://www.bet365.com/',
    'caesars': 'https://www.williamhill.com/us/',
    'betrivers': 'https://www.betrivers.com/',
    'bovada': 'https://www.bovada.lv/',
    'novig': 'https://novig.us/'
}

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        # Keep only last 1000 entries
        if len(history) > 1000:
            history = dict(list(history.items())[-1000:])
        json.dump(history, f, indent=4)

def iso_to_unix(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except Exception:
        return None

def format_odds(odds):
    return f"+{odds}" if odds > 0 else str(odds)

def american_to_implied(odds):
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)

def implied_to_american(prob):
    if prob <= 0 or prob >= 1: return None
    if prob > 0.5:
        return int(-round((prob / (1 - prob)) * 100))
    else:
        return int(round(((1 - prob) / prob) * 100))

def american_to_decimal(odds):
    if odds > 0:
        return (odds / 100) + 1
    else:
        return (100 / abs(odds)) + 1

def calculate_edge_metrics(sharp_odds_target, sharp_odds_opponent, soft_odds_target):
    ip_target = american_to_implied(sharp_odds_target)
    ip_opponent = american_to_implied(sharp_odds_opponent)
    if (ip_target + ip_opponent) == 0: return 0, 0, 0

    true_prob_win = ip_target / (ip_target + ip_opponent)
    true_prob_lose = 1 - true_prob_win
    fair_odds = implied_to_american(true_prob_win)
    
    profit = soft_odds_target if soft_odds_target > 0 else 100 / (abs(soft_odds_target) / 100)
    ev_percentage = round((true_prob_win * profit) - (true_prob_lose * 100), 2)
    
    b = american_to_decimal(soft_odds_target) - 1  
    full_kelly_fraction = (b * true_prob_win - true_prob_lose) / b if b > 0 else 0
    quarter_kelly = (full_kelly_fraction / 4) * 100 if full_kelly_fraction > 0 else 0
        
    return ev_percentage, round(quarter_kelly, 2), fair_odds

def analyze_game_for_ev(game, min_edge=1.0):
    ev_plays = []
    matchup = f"{game.get('home_team')} vs {game.get('away_team')}"
    sport_name = game.get('sport_title', 'Basketball')
    unix_time = iso_to_unix(game.get('commence_time'))

    bookmakers = game.get('bookmakers', [])
    pinnacle_data = next((b for b in bookmakers if b['key'] == 'pinnacle'), None)
    if not pinnacle_data: return ev_plays
        
    soft_books = [b for b in bookmakers if b['key'] != 'pinnacle']
    
    for book in soft_books:
        book_name = book['title']
        book_key = book['key']
        
        for market in book.get('markets', []):
            market_key = market['key']
            pinny_market = next((m for m in pinnacle_data.get('markets', []) if m['key'] == market_key), None)
            if not pinny_market: continue
                
            for soft_outcome in market['outcomes']:
                soft_odds = soft_outcome['price']
                if soft_odds > 250 or soft_odds < -500: continue
                
                name = soft_outcome['name']
                player = soft_outcome.get('description', '')
                point = soft_outcome.get('point')
                pinny_target = next((o for o in pinny_market['outcomes'] if o['name'] == name and o.get('description', '') == player and o.get('point') == point), None)
                if not pinny_target: continue
                    
                pinny_opponent = next((o for o in pinny_market['outcomes'] if o['name'] != name and o.get('description', '') == player and o.get('point') == point), None)
                if not pinny_opponent: continue
                    
                edge, q_kelly, fair_value = calculate_edge_metrics(pinny_target['price'], pinny_opponent['price'], soft_odds)
                if edge >= min_edge and q_kelly > 0:
                    bet_label = f"{player} {name} {point}" if player and point is not None else (f"{name} {point}" if point is not None else name)
                    market_label = market_key.replace('_', ' ').title()
                    bet_id = f"{game['id']}_{market_label}_{bet_label}_{book_name}_{soft_odds}".replace(" ", "_")
                    
                    ev_plays.append({
                        "id": bet_id, "game_id": game['id'], "matchup": matchup, "unix_time": unix_time,
                        "market": market_label, "bet": bet_label, "odds": soft_odds, "fair_odds": format_odds(fair_value),
                        "sportsbook": book_name, "book_key": book_key, "ev": edge, "kelly": q_kelly, "sport": sport_name
                    })
    return ev_plays

async def send_discord_alert_async(session, play):
    base_url = BOOKMAKER_LINKS.get(play['book_key'], 'https://google.com/search?q=')
    book_url = f"{base_url}{play['game_id']}" if play['book_key'] in ['draftkings', 'fanduel', 'betmgm'] else base_url
    action_link = f"🚀 **[OPEN IN APP]({book_url})**\n*(Long-press for App/APK redirect)*"
    tip_time_display = f"<t:{play['unix_time']}:F>" if play.get('unix_time') else "Time TBD"
    kelly_pct = play['kelly'] / 100
    stake_text = f"**$1 Unit:** ${kelly_pct * 100:.2f}\n**$10 Unit:** ${kelly_pct * 1000:.2f}\n**$100 Unit:** ${kelly_pct * 10000:.2f}"

    embed = {
        "title": f"🚨 {play['sport'].upper()} +EV Play! 🚨",
        "url": book_url, "color": 1500989, "author": {"name": "$BEE BAKED BETS"},
        "image": {"url": "https://pbs.twimg.com/media/HCG2WMSW0AA4zzN?format=jpg&name=900x900"},
        "fields": [
            {"name": "🏀 Matchup", "value": f"**{play['matchup']}**", "inline": True},
            {"name": "⏰ Tip-Off", "value": tip_time_display, "inline": True},
            {"name": "\u200B", "value": "\u200B", "inline": True}, 
            {"name": "🎯 Market", "value": play['market'], "inline": True},
            {"name": "👤 Bet", "value": f"**{play['bet']}**", "inline": True},
            {"name": "📊 Odds", "value": f"**{format_odds(play['odds'])}**", "inline": True},
            {"name": "⚖️ Fair Value", "value": play['fair_odds'], "inline": True},
            {"name": "🏦 Bookmaker", "value": play['sportsbook'], "inline": True},
            {"name": "📈 Est. Edge", "value": f"**{play['ev']}%**", "inline": True}, 
            {"name": "💰 Stakes (Q-Kelly)", "value": stake_text, "inline": False},
            {"name": "⚡ Action", "value": action_link, "inline": False}
        ],
        "footer": {"text": "$BEE BAKED BETS Premium Basketball Alerts"},
        "timestamp": datetime.now(timezone.utc).isoformat() 
    }
    payload = {"username": "$BEE BAKED BETS", "embeds": [embed]} 
    try:
        async with session.post(DISCORD_WEBHOOK_URL, json=payload) as response:
            if response.status == 429: await asyncio.sleep(5)
    except Exception as e: print(f"❌ Alert Failed: {e}")

async def main():
    print(f"🐝 Scanning NBA and NCAAB...")
    sent_history = load_history()
    async with aiohttp.ClientSession() as session:
        for league in LEAGUES_TO_SCAN:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
            params = {
                "apiKey": ODDS_API_KEY, "regions": "us",
                "markets": "h2h,spreads,totals", 
                "oddsFormat": "american",
                "bookmakers": "pinnacle,draftkings,fanduel,betmgm,bet365,caesars,betrivers"
            }
            async with session.get(url, params=params) as response:
                if response.status != 200: continue
                games = await response.json()
                for game in games:
                    plays = analyze_game_for_ev(game, min_edge=1.0)
                    for play in plays:
                        if play['id'] not in sent_history:
                            await send_discord_alert_async(session, play)
                            sent_history[play['id']] = {"odds": play['odds'], "fair": play['fair_odds'], "sent_at": datetime.now(timezone.utc).isoformat()}
                            await asyncio.sleep(1)
        save_history(sent_history)

if __name__ == "__main__":
    asyncio.run(main())