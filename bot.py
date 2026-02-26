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
    print("❌ Error: Missing API Key or Webhook URL. Check your GitHub Secrets.")
    sys.exit()
else:
    print("✅ Security credentials loaded successfully.")

# --- BOT CONFIGURATION ---
HISTORY_FILE = 'sent_alerts.json'

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

def format_odds(odds):
    return f"+{odds}" if odds > 0 else str(odds)

def american_to_implied(odds):
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)

def implied_to_american(prob):
    if prob == 0 or prob == 1: return None
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
    # 1. De-vig the sharp market
    ip_target = american_to_implied(sharp_odds_target)
    ip_opponent = american_to_implied(sharp_odds_opponent)
    
    # 🚀 IMPROVEMENT: Prevent division by zero if odds data is corrupted
    if (ip_target + ip_opponent) == 0:
        return 0, 0, 0

    true_prob_win = ip_target / (ip_target + ip_opponent)
    true_prob_lose = 1 - true_prob_win
    
    # 2. Calculate Fair Value Odds
    fair_odds = implied_to_american(true_prob_win)
    
    # 3. Calculate Expected Value (EV %)
    profit = soft_odds_target if soft_odds_target > 0 else 100 / (abs(soft_odds_target) / 100)
    ev_percentage = round((true_prob_win * profit) - (true_prob_lose * 100), 2)
    
    # 4. Calculate Quarter Kelly
    b = american_to_decimal(soft_odds_target) - 1  
    full_kelly_fraction = (b * true_prob_win - true_prob_lose) / b if b > 0 else 0
    quarter_kelly = (full_kelly_fraction / 4) * 100 if full_kelly_fraction > 0 else 0
        
    return ev_percentage, round(quarter_kelly, 2), fair_odds

def analyze_game_for_ev(game, min_edge=4.0):
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
                                     
                if not pinny_target or pinny_target['price'] > 200 or pinny_target['price'] < -200:
                    continue
                    
                pinny_opponent = next((o for o in pinny_market['outcomes'] 
                                       if o['name'] != name 
                                       and o.get('description', '') == player 
                                       and o.get('point') == point), None)
                                       
                if not pinny_opponent:
                    continue
                    
                edge, q_kelly, fair_value = calculate_edge_metrics(pinny_target['price'], pinny_opponent['price'], soft_odds)
                
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
                        "odds": format_odds(soft_odds),
                        "fair_odds": format_odds(fair_value),
                        "sportsbook": book_name,
                        "book_key": book_key,
                        "ev": edge,
                        "kelly": q_kelly
                    })
                    
    return ev_plays

async def send_discord_alert_async(session, play):
    book_url = BOOKMAKER_LINKS.get(play['book_key'], 'https://google.com/search?q=sportsbook')
    action_link = f"📱 [Place Bet Here]({book_url})"
    tip_time_display = f"<t:{play['unix_time']}:F>" if play.get('unix_time') else "Time TBD"

    kelly_pct = play['kelly'] / 100
    stake_text = f"**$1 Unit:** ${kelly_pct * 100:.2f}\n**$10 Unit:** ${kelly_pct * 1000:.2f}\n**$100 Unit:** ${kelly_pct * 10000:.2f}"

    embed = {
        "title": "🚨 High-Value +EV Target Acquired 🚨",
        "color": 16766720,
        "fields": [
            {"name": "Matchup", "value": play['matchup'], "inline": False},
            {"name": "Tip-Off", "value": tip_time_display, "inline": False},
            {"name": "Market", "value": play['market'], "inline": True},
            {"name": "Bet", "value": play['bet'], "inline": True},
            {"name": "Odds", "value": play['odds'], "inline": True},
            {"name": "Fair Value", "value": play['fair_odds'], "inline": True},
            {"name": "Book", "value": play['sportsbook'], "inline": True},
            {"name": "Estimated Edge", "value": f"{play['ev']}%", "inline": True},
            {"name": "Q-Kelly Stakes", "value": stake_text, "inline": True},
            {"name": "Action", "value": action_link, "inline": False}
        ],
        "footer": {"text": "Honey Bee Money Premium Alerts"}
    }
    payload = {"username": "beebaked EV Bot", "embeds": [embed]}
    
    # 🚀 IMPROVEMENT: Added try/except so a failed Discord ping doesn't crash the whole bot
    try:
        async with session.post(DISCORD_WEBHOOK_URL, json=payload) as response:
            if response.status == 429:
                print("⚠️ Discord Rate Limit hit. Pausing for 5 seconds...")
                await asyncio.sleep(5)
            else:
                response.raise_for_status()
    except Exception as e:
        print(f"❌ Failed to send Discord alert: {e}")

async def main():
    print("Fetching live NCAAB games and odds...")
    
    async with aiohttp.ClientSession() as session:
        # 🚀 IMPROVEMENT: Use the `/odds` endpoint to fetch ALL games in ONE single API call!
        url_all_odds = f"https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds/"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "us,us_ex,us2,eu", 
            "markets": "h2h,player_points,player_rebounds,player_assists", 
            "oddsFormat": "american",
            "bookmakers": "pinnacle,draftkings,fanduel,betmgm,espnbet,bet365,kalshi,novig" 
        }
        
        try:
            async with session.get(url_all_odds, params=params) as response:
                if response.status != 200:
                    print(f"❌ API Error: HTTP {response.status}")
                    return
                games_data = await response.json()
        except Exception as e:
            print(f"❌ Network Error fetching data: {e}")
            return

        if not games_data:
            print("No live games found.")
            return

        now_unix = int(time.time())
        twenty_four_hours_from_now = now_unix + (24 * 60 * 60)
        
        all_profitable_plays = []
        
        # 🚀 IMPROVEMENT: Streamlined the filtering and processing loop
        for game in games_data:
            commence_time = game.get('commence_time')
            if commence_time:
                unix_time = iso_to_unix(commence_time)
                # Only scan games starting in the next 24 hours
                if unix_time and now_unix <= unix_time <= twenty_four_hours_from_now:
                    plays = analyze_game_for_ev(game, min_edge=1.0)
                    all_profitable_plays.extend(plays)
        
        print(f"⚡ Scanned {len(games_data)} total games. Found {len(all_profitable_plays)} total edges.")
        
        if all_profitable_plays:
            sent_history = load_history()
            new_alerts_sent = False
            
            for play in all_profitable_plays:
                if play['id'] not in sent_history:
                    await send_discord_alert_async(session, play)
                    print(f"Alert sent: {play['bet']} at {play['sportsbook']}")
                    sent_history.append(play['id'])
                    new_alerts_sent = True
                    await asyncio.sleep(1.5) # Protects against Discord rate limits
                
            if new_alerts_sent:
                save_history(sent_history[-1000:]) 
                print("✅ New alerts successfully saved to history file.")
        else:
            print("No +EV plays found above the 4.0% threshold in this scan.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
        print("✅ Scan complete. Shutting down gracefully.")
    except Exception as e:
        print(f"❌ Script failed: {e}")