import json
import os
import sys
import time
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# --- SECURE CREDENTIAL LOADING ---
load_dotenv()

ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')

if not ODDS_API_KEY or not DISCORD_WEBHOOK_URL:
    print("❌ Error: Missing API Key or Webhook URL. Check your GitHub Secrets.")
    sys.exit()

# --- SMART CONFIGURATION ---
LEAGUES_TO_SCAN = ['basketball_nba', 'basketball_ncaab', 'icehockey_nhl']
HISTORY_FILE = 'sent_alerts.json'
HEARTBEAT_INTERVAL_HOURS = 24  # 🚀 UPDATED: Now once every 24 hours

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

# --- HELPER FUNCTIONS ---

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        if len(history) > 1000:
            last_h = history.get('last_heartbeat')
            history = dict(list(history.items())[-1000:])
            if last_h: history['last_heartbeat'] = last_h
        json.dump(history, f, indent=4)

def format_odds(odds):
    return f"+{odds}" if odds > 0 else str(odds)

def american_to_implied(odds):
    return (100 / (odds + 100)) if odds > 0 else (abs(odds) / (abs(odds) + 100))

def implied_to_american(prob):
    if prob <= 0 or prob >= 1: return None
    return int(-round((prob / (1 - prob)) * 100)) if prob > 0.5 else int(round(((1 - prob) / prob) * 100))

def calculate_edge_metrics(sharp_odds_target, sharp_odds_opponent, soft_odds_target):
    ip_t = american_to_implied(sharp_odds_target)
    ip_o = american_to_implied(sharp_odds_opponent)
    if (ip_t + ip_o) == 0: return 0, 0, 0
    
    true_prob = ip_t / (ip_t + ip_o)
    profit = soft_odds_target if soft_odds_target > 0 else 100 / (abs(soft_odds_target) / 100)
    ev = round((true_prob * profit) - ((1 - true_prob) * 100), 2)
    
    dec = (soft_odds_target/100 + 1) if soft_odds_target > 0 else (100/abs(soft_odds_target) + 1)
    k = round((((dec-1) * true_prob - (1-true_prob)) / (dec-1) / 4) * 100, 2)
    return ev, k, implied_to_american(true_prob)

# --- CORE LOGIC ---

def analyze_game_for_ev(game, min_edge=1.0):
    ev_plays = []
    pinnacle = next((b for b in game.get('bookmakers', []) if b['key'] == 'pinnacle'), None)
    if not pinnacle: return ev_plays
    
    for book in [b for b in game.get('bookmakers', []) if b['key'] != 'pinnacle']:
        for market in book.get('markets', []):
            pin_m = next((m for m in pinnacle.get('markets', []) if m['key'] == market['key']), None)
            if not pin_m: continue
            for soft_out in market['outcomes']:
                price = soft_out['price']
                if price > 250 or price < -500: continue
                
                pin_t = next((o for o in pin_m['outcomes'] if o['name'] == soft_out['name'] and o.get('point') == soft_out.get('point')), None)
                pin_o = next((o for o in pin_m['outcomes'] if o['name'] != soft_out['name'] and o.get('point') == soft_out.get('point')), None)
                
                if pin_t and pin_o:
                    edge, q_k, fair = calculate_edge_metrics(pin_t['price'], pin_o['price'], price)
                    if edge >= min_edge and q_k > 0:
                        bet_label = f"{soft_out.get('description', '')} {soft_out['name']} {soft_out.get('point', '')}".strip()
                        ev_plays.append({
                            "id": f"{game['id']}_{market['key']}_{bet_label}_{book['title']}_{price}",
                            "matchup": f"{game['home_team']} vs {game['away_team']}",
                            "bet": bet_label, "ev": edge, "kelly": q_k, "odds": price, "fair": format_odds(fair),
                            "book": book['title'], "book_key": book['key'], "sport": game['sport_title'], "game_id": game['id'],
                            "unix": int(datetime.fromisoformat(game['commence_time'].replace('Z', '+00:00')).timestamp())
                        })
    return ev_plays

async def send_heartbeat(session):
    embed = {
        "title": "🐝 $BEE BAKED BETS: Daily Status Update",
        "description": "The hive is active and scanning markets for **NBA, NCAAB, and NHL**. \n\n*Status:* **All Systems Go.** \n*Market Condition:* **Efficient.**",
        "color": 16776960,
        "footer": {"text": "24-Hour Check-in • Monitoring 24/7"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    payload = {"username": "$BEE BAKED BETS", "embeds": [embed]}
    async with session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
        return resp.status == 204

async def send_discord_alert_async(session, play):
    base_url = BOOKMAKER_LINKS.get(play['book_key'], 'https://google.com/search?q=')
    book_url = f"{base_url}{play['game_id']}" if play['book_key'] in ['draftkings', 'fanduel', 'betmgm'] else base_url
    action_link = f"🚀 **[OPEN IN APP]({book_url})**\n*(Long-press for App/APK redirect)*"
    tip_time = f"<t:{play['unix']}:F>"
    
    k_pct = play['kelly'] / 100
    stake_text = f"**$1 Unit:** ${k_pct * 100:.2f}\n**$10 Unit:** ${k_pct * 1000:.2f}\n**$100 Unit:** ${k_pct * 10000:.2f}"

    embed = {
        "title": f"🚨 {play['sport'].upper()} +EV Play! 🚨",
        "url": book_url, "color": 1500989, "author": {"name": "$BEE BAKED BETS"},
        "image": {"url": "https://pbs.twimg.com/media/HCG2WMSW0AA4zzN?format=jpg&name=900x900"},
        "fields": [
            {"name": "🏀 Matchup", "value": f"**{play['matchup']}**", "inline": True},
            {"name": "⏰ Tip-Off", "value": tip_time, "inline": True},
            {"name": "\u200B", "value": "\u200B", "inline": True}, 
            {"name": "🎯 Market", "value": play['market'], "inline": True},
            {"name": "👤 Bet", "value": f"**{play['bet']}**", "inline": True},
            {"name": "📊 Odds", "value": f"**{format_odds(play['odds'])}**", "inline": True},
            {"name": "⚖️ Fair Value", "value": play['fair'], "inline": True},
            {"name": "🏦 Bookmaker", "value": play['book'], "inline": True},
            {"name": "📈 Est. Edge", "value": f"**{play['ev']}%**", "inline": True}, 
            {"name": "💰 Stakes (Q-Kelly)", "value": stake_text, "inline": False},
            {"name": "⚡ Action", "value": action_link, "inline": False}
        ],
        "footer": {"text": "$BEE BAKED BETS Premium Alerts"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    payload = {"username": "$BEE BAKED BETS", "embeds": [embed]}
    try:
        async with session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
            if resp.status == 429: await asyncio.sleep(5)
    except Exception as e: print(f"❌ Alert Failed: {e}")

async def main():
    print(f"🐝 Scanning Markets...")
    sent_history = load_history()
    async with aiohttp.ClientSession() as session:
        any_new = False
        for league in LEAGUES_TO_SCAN:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
            params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    games = await resp.json()
                    for game in games:
                        for play in analyze_game_for_ev(game):
                            if play['id'] not in sent_history:
                                await send_discord_alert_async(session, play)
                                sent_history[play['id']] = {"sent_at": datetime.now(timezone.utc).isoformat()}
                                any_new = True
                                await asyncio.sleep(1)
        
        # Heartbeat Logic (24-Hour Cycle)
        last_h_str = sent_history.get('last_heartbeat')
        should_h = False
        if not last_h_str: should_h = True
        else:
            last_h = datetime.fromisoformat(last_h_str)
            if datetime.now(timezone.utc) > last_h + timedelta(hours=HEARTBEAT_INTERVAL_HOURS): should_h = True
            
        if not any_new and should_h:
            await send_heartbeat(session)
            sent_history['last_heartbeat'] = datetime.now(timezone.utc).isoformat()
        elif any_new:
            # If we find bets, we "reset" the heartbeat timer to stay quiet for another 24h
            sent_history['last_heartbeat'] = datetime.now(timezone.utc).isoformat()
            
        save_history(sent_history)

if __name__ == "__main__":
    asyncio.run(main())