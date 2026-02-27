import json
import os
import sys
import time
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')

# --- CONFIG ---
LEAGUES_TO_SCAN = ['basketball_nba', 'basketball_ncaab', 'icehockey_nhl']
HISTORY_FILE = 'sent_alerts.json'
HEARTBEAT_INTERVAL_HOURS = 4  # Send a "System Live" message every 4 hours

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f: return json.load(f)
        except: return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        # Keep only the last 1000 entries + the heartbeat timestamp
        if len(history) > 1000:
            heartbeat = history.get('last_heartbeat')
            history = dict(list(history.items())[-1000:])
            if heartbeat: history['last_heartbeat'] = heartbeat
        json.dump(history, f, indent=4)

async def send_heartbeat(session):
    """Sends a system status update to Discord."""
    embed = {
        "title": "🐝 $BEE BAKED BETS: System Status",
        "description": "The hive is active and scanning markets for **NBA, NCAAB, and NHL**. \n\n*Current Market Status:* **Efficient** (No +EV edges found in this cycle).",
        "color": 16776960, # Yellow
        "footer": {"text": "Monitoring 24/7 • Next scan in 15 mins"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    payload = {"username": "$BEE BAKED BETS", "embeds": [embed]}
    async with session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
        return resp.status == 204

async def main():
    print(f"🐝 Scanning Markets...")
    sent_history = load_history()
    last_heartbeat_str = sent_history.get('last_heartbeat')
    
    # Heartbeat logic
    should_send_heartbeat = False
    if not last_heartbeat_str:
        should_send_heartbeat = True
    else:
        last_heartbeat = datetime.fromisoformat(last_heartbeat_str)
        if datetime.now(timezone.utc) > last_heartbeat + timedelta(hours=HEARTBEAT_INTERVAL_HOURS):
            should_send_heartbeat = True

    async with aiohttp.ClientSession() as session:
        any_new_bets = False
        
        # --- SCANNING LOGIC ---
        for league in LEAGUES_TO_SCAN:
            url = f"https://api.the-odds-api.com/v4/sports/{league}/odds/"
            params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    games = await resp.json()
                    for game in games:
                        plays = analyze_game_for_ev(game, min_edge=0.5)
                        for play in plays:
                            if play['id'] not in sent_history:
                                await send_discord_alert_async(session, play)
                                sent_history[play['id']] = {"odds": play['odds'], "sent_at": datetime.now(timezone.utc).isoformat()}
                                any_new_bets = True
                                await asyncio.sleep(1)

        # Handle Heartbeat if no bets were found
        if not any_new_bets and should_send_heartbeat:
            print("Sending Heartbeat...")
            await send_heartbeat(session)
            sent_history['last_heartbeat'] = datetime.now(timezone.utc).isoformat()
            save_history(sent_history)
        elif any_new_bets:
            # If we sent a real bet, reset the heartbeat timer so we don't spam
            sent_history['last_heartbeat'] = datetime.now(timezone.utc).isoformat()
            save_history(sent_history)

# (Keep your existing analyze_game_for_ev and send_discord_alert_async functions here)

if __name__ == "__main__":
    asyncio.run(main())