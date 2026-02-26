import json
import os
import sys
import time
import asyncio
import aiohttp
from datetime import datetime
from dotenv import load_dotenv

# --- SECURE CREDENTIAL LOADING ---
load_dotenv()

ODDS_API_KEY = os.environ.get('ODDS_API_KEY')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')

if not ODDS_API_KEY or not DISCORD_WEBHOOK_URL:
    print("❌ Error: Missing API Key or Webhook URL. Check your .env file or GitHub Secrets.")
    sys.exit()
else:
    print("✅ Security credentials loaded successfully.")

# --- BOT CONFIGURATION ---
HISTORY_FILE = 'sent_alerts.json'
MAX_GAMES_TO_SCAN = 50 # Bumped up to 50 so you can see the speed difference!

BOOKMAKER_LINKS = {
    'draftkings': 'https://sportsbook.draftkings.com',
    'fanduel': 'https://sportsbook.fanduel.com',
    'betmgm': 'https://sports.betmgm.com',
    'espnbet': 'https://espnbet.com',
    'bet365': 'https://www.bet365.com',
    'kalshi': 'https://kalshi.com',
    'novig': 'https://novig.us'
}

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f)

def iso_to_unix(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return int(dt.timestamp())
    except Exception:
        return None

def american_to_implied(odds):
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)

def american_to_decimal(odds):
    if odds > 0:
        return (odds / 100) + 1
    else:
        return (100 / abs(odds)) + 1

def calculate_ev_and_kelly(sharp_odds_target, sharp_odds_opponent, soft_odds_target):
    ip_target = american_to_implied(sharp_odds_target)
    ip_opponent = american_to_implied(sharp_odds_opponent)
    true_prob_win = ip_target / (ip_target + ip_opponent)
    true_prob_lose = 1 - true_prob_win
    
    profit = soft_odds_target if soft_odds_target > 0 else 100 / (abs(soft_odds_target) / 100)
    ev_percentage = round((true_prob_win * profit) - (true_prob_lose * 100), 2)
    
    b = american_to_decimal(soft_odds_target) - 1  
    full_kelly_fraction = (b * true_prob_win - true_prob_lose) / b if b > 0 else 0
    quarter_kelly = (full_kelly_fraction / 4) * 100 if full_kelly_fraction > 0 else 0
        
    return ev_percentage, round(quarter_kelly, 2)

def analyze_game_for_ev(game, min_edge=2.0):
    ev_plays = []
    matchup = f"{game.get('home_team')} vs {game.get('away_team')}"
    
    commence_time = game.get('commence_time')
    unix_time = iso_to_unix(commence_time) if commence_time else None

    bookmakers = game.get('bookmakers', [])
    pinnacle_data = next((b for b in bookmakers if b['key'] == 'pinnacle'), None)
    if not pinnacle_data:
        return ev_plays
        
    soft_books = [b for b in bookmakers if b['key'] != 'pinnacle']
    
    for book in soft_books:
        book_name = book['title']
        book_key = book['key']
        
        for market in book.get('markets', []):
            market_key = market['key']
            
            pinny_market = next((m for m in pinnacle_data.get('markets', []) if m['key'] == market_key), None)
            if not pinny_market:
                continue
                
            for soft_outcome in market['outcomes']:
                name = soft_outcome['name']
                player = soft_outcome.get('description', '')
                point = soft_outcome.get('point')
                soft_odds = soft_outcome['price']
                
                if soft_odds > 200 or soft_odds < -200:
                    continue
                    
                pinny_target = next((o for o in pinny_market['outcomes'] 
                                     if o['name'] == name 
                                     and o.get('description', '') == player 
                                     and o.get('point') == point), None)
                                     
                if not pinny_target:
                    continue
                
                if pinny_target['price'] > 200 or pinny_target['price'] < -200:
                    continue
                    
                pinny_opponent = next((o for o in pinny_market['outcomes'] 
                                       if o['name'] != name 
                                       and o.get('description', '') == player 
                                       and o.get('point') == point), None)
                                       
                if not pinny_opponent:
                    continue
                    
                edge, q_kelly = calculate_ev_and_kelly(pinny_target['price'], pinny_opponent['price'], soft_odds)
                
                if edge >= min_edge and q_kelly > 0:
                    bet_label = f"{player} {name} {point}" if player and point is not None else name
                    market_label = market_key.replace('_', ' ').title()
                    
                    bet_id = f"{matchup}_{market_label}_{bet_label}_{book_name}_{soft_odds}".replace(" ", "_")
                    
                    ev_plays.append({
                        "id": bet_id,
                        "matchup": matchup,
                        "unix_time": unix_time,
                        "market": market_label,
                        "bet": bet_label,
                        "odds": f"{'+' if soft_odds > 0 else ''}{soft_odds}",
                        "sportsbook": book_name,
                        "book_key": book_key,
                        "ev": edge,
                        "kelly": q_kelly
                    })
                    
    return ev_plays

async def fetch_odds_for_game(session, event_id, semaphore):
    """Fetches odds for a single game concurrently, respecting the rate limit."""
    url_odds = f"https://api.the-odds-api.com/v4/sports/basketball_ncaab/events/{event_id}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us,us_ex,us2,eu", 
        "markets": "h2h,player_points,player_rebounds,player_assists", 
        "oddsFormat": "american",
        "bookmakers": "pinnacle,draftkings,fanduel,betmgm,espnbet,bet365,kalshi,novig" 
    }
    
    # The semaphore ensures only 5 of these blocks run at the exact same time
    async with semaphore:
        async with session.get(url_odds, params=params) as response:
            if response.status == 200:
                data = await response.json()
                return analyze_game_for_ev(data, min_edge=4.0)
            else:
                print(f"⚠️ Failed to fetch odds for game {event_id}: HTTP {response.status}")
                return []

async def send_discord_alert_async(session, play):
    book_url = BOOKMAKER_LINKS.get(play['book_key'], 'https://google.com/search?q=sportsbook')
    action_link = f"📱 [Place Bet Here]({book_url})"
    tip_time_display = f"<t:{play['unix_time']}:F>" if play.get('unix_time') else "Time TBD"

    kelly_pct = play['kelly'] / 100
    stake_1 = kelly_pct * 100
    stake_10 = kelly_pct * 1000
    stake_100 = kelly_pct * 10000
    
    stake_text = f"**$1 Unit:** ${stake_1:.2f}\n**$10 Unit:** ${stake_10:.2f}\n**$100 Unit:** ${stake_100:.2f}"

    embed = {
        "title": "🚨 High-Value +EV Target Acquired 🚨",
        "color": 16766720,
        "fields": [
            {"name": "Matchup", "value": play['matchup'], "inline": False},
            {"name": "Tip-Off", "value": tip_time_display, "inline": False},
            {"name": "Market", "value": play['market'], "inline": True},
            {"name": "Bet", "value": play['bet'], "inline": True},
            {"name": "Odds", "value": str(play['odds']), "inline": True},
            {"name": "Book", "value": play['sportsbook'], "inline": True},
            {"name": "Estimated Edge", "value": f"{play['ev']}%", "inline": True},
            {"name": "Q-Kelly Stakes", "value": stake_text, "inline": True},
            {"name": "Action", "value": action_link, "inline": False}
        ],
        "footer": {"text": "Honey Bee Money Premium Alerts"}
    }
    payload = {"username": "beebaked EV Bot", "embeds": [embed]}
    
    try:
        async with session.post(DISCORD_WEBHOOK_URL, json=payload) as response:
            response.raise_for_status()
    except Exception as e:
        print(f"❌ Failed to send Discord alert: {e}")

async def main():
    print("Fetching live NCAAB games...")
    
    # We use a single ClientSession for all our requests, which is much faster/more efficient
    async with aiohttp.ClientSession() as session:
        url_events = f"https://api.the-odds-api.com/v4/sports/basketball_ncaab/events/?apiKey={ODDS_API_KEY}"
        
        async with session.get(url_events) as events_response:
            events = await events_response.json() if events_response.status == 200 else []
        
        if events:
            events_to_scan = events[:MAX_GAMES_TO_SCAN] if MAX_GAMES_TO_SCAN else events
            print(f"⚡ Asynchronously scanning {len(events_to_scan)} games for deep market edges...")
            
            # Create a Semaphore to limit concurrent API calls to 5 at a time
            semaphore = asyncio.Semaphore(5)
            
            # Spin up all the fetching tasks simultaneously
            tasks = [fetch_odds_for_game(session, event['id'], semaphore) for event in events_to_scan]
            
            # Wait for all tasks to finish and gather the results
            results = await asyncio.gather(*tasks)
            
            # 'results' is a list of lists. We need to flatten it into one big list of plays.
            all_profitable_plays = [play for sublist in results for play in sublist]
            
            if all_profitable_plays:
                sent_history = load_history()
                new_alerts_sent = False
                
                # We process Discord alerts one by one to respect Discord's rate limit
                for play in all_profitable_plays:
                    if play['id'] not in sent_history:
                        await send_discord_alert_async(session, play)
                        print(f"Alert sent: {play['bet']} at {play['sportsbook']}")
                        sent_history.append(play['id'])
                        new_alerts_sent = True
                        await asyncio.sleep(1.5) # Protects against Discord HTTP 429
                    else:
                        print(f"Skipping duplicate: {play['bet']}")
                        
                if new_alerts_sent:
                    save_history(sent_history[-500:])
            else:
                print("No +EV plays found above the 4.0% threshold in this scan.")
        else:
            print("No live games found.")

if __name__ == "__main__":
    # This is how you start an async program in Python
    asyncio.run(main())