import os
import requests
import csv
import argparse
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

REGIONS = 'us,eu'
BOOKMAKERS = 'fanduel,draftkings,bet365,pinnacle'
ODDS_FORMAT = 'decimal'

# --- SPORT CONFIGURATIONS ---
# FIXED: Removed 'player_' markets to prevent 422 errors. 
# FIXED: Updated sport keys for Esports and Tennis.
SPORT_CONFIGS = {
    "nba": {
        "api_key": "basketball_nba",
        "markets": "h2h,spreads,totals", 
        "icon": "🏀",
        "color": 5763719,
        "emergency_color": 15158332,
        "name": "NBA"
    },
    "mlb": {
        "api_key": "baseball_mlb",
        "markets": "h2h,spreads,totals",
        "icon": "⚾",
        "color": 10038562,
        "emergency_color": 15158332,
        "name": "MLB"
    },
    "nhl": {
        "api_key": "icehockey_nhl",
        "markets": "h2h,spreads,totals",
        "icon": "🏒",
        "color": 1146986,
        "emergency_color": 15158332,
        "name": "NHL"
    },
    "ncaab": {
        "api_key": "basketball_ncaab",
        "markets": "h2h,spreads,totals",
        "icon": "🎓",
        "color": 3447003,
        "emergency_color": 15158332,
        "name": "NCAAB"
    },
    "soccer": {
        "api_key": "soccer_epl",
        "markets": "h2h,spreads,totals",
        "icon": "⚽",
        "color": 3066993,
        "emergency_color": 15158332,
        "name": "EPL"
    },
    "mma": {
        "api_key": "mma_mixed_martial_arts",
        "markets": "h2h,totals",
        "icon": "🥊",
        "color": 10038562,
        "emergency_color": 15158332,
        "name": "MMA"
    },
    "esports": {
        # UPDATED: 'esports_csgo' is now 'esports_counterstrike'
        "api_key": "esports_counterstrike",
        "markets": "h2h",
        "icon": "🎮",
        "color": 10181046,
        "emergency_color": 15158332,
        "name": "CS2"
    },
    "tennis": {
        # UPDATED: The Odds API often uses specific tournament keys (e.g., tennis_atp_wimbledon)
        # Use 'tennis_atp_aus_open_singles' or check active keys in the API docs.
        "api_key": "tennis_atp_french_open", 
        "markets": "h2h",
        "icon": "🎾",
        "color": 11001111,
        "emergency_color": 15158332,
        "name": "ATP"
    }
}

# --- HELPERS (Same as before) ---
def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def log_bet_to_csv(matchup, market, selection, odds, ev_val, units, fair_price):
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle'])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"), 
            matchup, market, selection, odds, 
            f"{ev_val*100:.2f}%", units, fair_price, ""
        ])

def send_alert(p):
    if not DISCORD_WEBHOOK_URL: return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={
            "content": "@everyone" if p["is_emergency"] else "", 
            "embeds": [{"description": p["msg"], "color": p["color"], "image": {"url": FOOTER_IMG}}]
        })
    except Exception as e:
        print(f"Webhook Failed: {e}")

# --- CORE SCANNER (Same logic, now with supported markets) ---
def scan_sport(sport_key):
    config = SPORT_CONFIGS.get(sport_key)
    if not config: return

    print(f"Scanning {config['name']}...")
    if not ODDS_API_KEY: return

    picks = []
    url = f"https://api.the-odds-api.com/v4/sports/{config['api_key']}/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': config['markets'], 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT}

    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code != 200: 
            print(f"API Error ({res.status_code}): {res.text}")
            return
        
        data = res.json()
        for game in data:
            matchup = f"{game.get('away_team', 'Unknown')} @ {game.get('home_team', 'Unknown')}"
            market_groups = {}
            for bm in game.get('bookmakers', []):
                name, title = bm['key'], bm['title']
                for mkt in bm.get('markets', []):
                    m_key = mkt['key']
                    for outcome in mkt['outcomes']:
                        label = f"{outcome.get('description', '')} {outcome['name']}".strip()
                        price, point = outcome['price'], outcome.get('point', '')
                        gid = f"{m_key}_{abs(float(point))}" if m_key == 'spreads' and point != '' else f"{m_key}_{point}"
                        
                        if gid not in market_groups: market_groups[gid] = {'sharp': {}, 'soft': {}}
                        if name == 'pinnacle': 
                            market_groups[gid]['sharp'][label] = price
                        else:
                            if label not in market_groups[gid]['soft'] or price > market_groups[gid]['soft'][label]['price']:
                                market_groups[gid]['soft'][label] = {'price': price, 'book': title, 'point': point}

            for gid, val in market_groups.items():
                sharp, soft = val['sharp'], val['soft']
                if len(sharp) == 2:
                    teams = list(sharp.keys())
                    p1, p2 = sharp[teams[0]], sharp[teams[1]]
                    vig = (1/p1) + (1/p2)
                    probs = {teams[0]: (1/p1)/vig, teams[1]: (1/p2)/vig}
                    
                    for t in teams:
                        if t in soft:
                            s_price = soft[t]['price']
                            ev = (s_price * probs[t]) - 1
                            if ev > 0.01:
                                units = min((ev / (s_price - 1)) / 4 * 100, 5.0)
                                m_label = gid.split('_')[0].upper()
                                pt = f" {soft[t]['point']}" if soft[t]['point'] != '' else ""
                                is_emergency = ev >= 0.05
                                
                                fair_american = to_american(1/probs[t])
                                log_bet_to_csv(matchup, m_label, f"{t}{pt}", to_american(s_price), ev, f"{units:.2f}", fair_american)

                                header = f"🚨 **{config['name']} EMERGENCY** 🚨" if is_emergency else f"{config['icon']} **{config['name']} +EV ALERT** {config['icon']}"
                                picks.append({
                                    "msg": f"{header}\n**Edge:** {ev*100:.2f}%\n**Match:** {matchup}\n**Market:** {m_label} | {t}{pt}\n**Book:** {soft[t]['book']} @ {to_american(s_price)}\n**Suggested:** {units:.2f} Units",
                                    "color": config['emergency_color'] if is_emergency else config['color'],
                                    "is_emergency": is_emergency
                                })
        if picks:
            for p in picks: send_alert(p)
            
    except Exception as e:
        print(f"Error scanning {config['name']}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sport", type=str, default="all")
    args = parser.parse_args()

    if args.sport == "all":
        for sport in SPORT_CONFIGS.keys(): scan_sport(sport)
    else:
        scan_sport(args.sport)