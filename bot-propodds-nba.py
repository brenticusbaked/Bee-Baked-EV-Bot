import os
import requests
import csv
from datetime import datetime
import time

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# Notice we are calling the new secret you just made!
PROP_ODDS_API_KEY = os.getenv("PROP_ODDS_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

# --- API PARAMS ---
SPORT = 'nba'
# Prop-Odds uses specific market names
MARKETS = ['player_points', 'player_assists'] 

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, ev_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet'])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{ev_val*100:.2f}%", units, fair_price
        ])

def get_nba_games(date_str):
    """Fetches today's NBA game IDs from Prop-Odds."""
    url = f"https://api.prop-odds.com/beta/games/{SPORT}?date={date_str}&api_key={PROP_ODDS_API_KEY}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json().get('games', [])
    except Exception as e:
        print(f"Error fetching games: {e}")
    return []

def get_prop_edges():
    if not PROP_ODDS_API_KEY: 
        print("Missing Prop-Odds API Key!")
        return []
        
    picks = []
    today = datetime.now().strftime("%Y-%m-%d")
    games = get_nba_games(today)
    
    if not games:
        return []

    for game in games:
        game_id = game['game_id']
        matchup = f"{game['away_team']} @ {game['home_team']}"
        
        for market in MARKETS:
            # Ping the odds endpoint for this specific game and market
            url = f"https://api.prop-odds.com/beta/odds/{game_id}/{market}?api_key={PROP_ODDS_API_KEY}"
            try:
                res = requests.get(url, timeout=10)
                if res.status_code != 200:
                    continue
                
                data = res.json()
                sportsbooks = data.get('sportsbooks', [])
                
                # We need to find pinnacle (sharp) and compare it to others (soft)
                market_dict = {}
                for book in sportsbooks:
                    book_id = book['bookmaker_id']
                    for outcome in book.get('market', {}).get('outcomes', []):
                        # Construct a unique ID for the player + line (e.g., "LeBron James_Over_25.5")
                        player = outcome['participant_name']
                        handicap = outcome.get('handicap', '')
                        name = outcome['name'] # Over or Under
                        price = outcome['odds'] # Prop-Odds returns decimal odds natively
                        
                        unique_id = f"{player}_{handicap}"
                        if unique_id not in market_dict:
                            market_dict[unique_id] = {'sharp': {}, 'soft': {}}
                            
                        if book_id == 'pinnacle':
                            market_dict[unique_id]['sharp'][name] = price
                        elif book_id in ['fanduel', 'draftkings', 'betm365', 'betmgm']:
                            if name not in market_dict[unique_id]['soft'] or price > market_dict[unique_id]['soft'][name]['price']:
                                market_dict[unique_id]['soft'][name] = {'price': price, 'book': book_id}

                # Math Engine: Calculate EV against Pinnacle's fair value
                for uid, val in market_dict.items():
                    sharp = val['sharp']
                    soft = val['soft']
                    
                    if 'Over' in sharp and 'Under' in sharp:
                        p_over = sharp['Over']
                        p_under = sharp['Under']
                        
                        # Remove the vig (the bookmaker's juice)
                        vig = (1/p_over) + (1/p_under)
                        true_prob_over = (1/p_over) / vig
                        true_prob_under = (1/p_under) / vig
                        
                        probs = {'Over': true_prob_over, 'Under': true_prob_under}
                        
                        for side in ['Over', 'Under']:
                            if side in soft:
                                s_price = soft[side]['price']
                                ev = (s_price * probs[side]) - 1
                                
                                # We want a minimum 2% edge for player props due to variance
                                if ev > 0.02: 
                                    units = min((ev / (s_price - 1)) / 4 * 100, 5.0)
                                    player_name, handicap = uid.split('_')
                                    is_emergency = ev >= 0.06
                                    
                                    fair_american = to_american(1/probs[side])
                                    log_bet_to_csv(matchup, market.upper(), f"{player_name} {side} {handicap}", to_american(s_price), ev, f"{units:.2f}", fair_american)
                                    
                                    header = "🚨 **PROP EMERGENCY** 🚨" if is_emergency else "🎯 **NBA PROP ALERT** 🎯"
                                    picks.append({
                                        "msg": f"{header}\n**Edge:** {ev*100:.2f}%\n**Match:** {matchup}\n**Market:** {market.upper()} | {player_name} {side} {handicap}\n**Book:** {soft[side]['book'].upper()} @ {to_american(s_price)}\n**Suggested:** {units:.2f} Units",
                                        "color": 15158332 if is_emergency else 3447003,
                                        "is_emergency": is_emergency
                                    })
            except Exception as e:
                print(f"Error parsing market {market}: {e}")
            
            # Rate limiting protection so we don't spam the API too fast
            time.sleep(0.5) 
            
    return picks

def send_alert(p):
    if not DISCORD_WEBHOOK_URL: return
    requests.post(DISCORD_WEBHOOK_URL, json={"content": "@everyone" if p["is_emergency"] else "", "embeds": [{"description": p["msg"], "color": p["color"], "image": {"url": FOOTER_IMG}}]})

def main():
    picks = get_prop_edges()
    if picks:
        for p in picks: send_alert(p)
    else:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "🎯 **Prop-Odds Scan Complete:** No NBA player prop edges found.", "color": 3447003, "image": {"url": FOOTER_IMG}}]})

if __name__ == "__main__":
    main()