import os
import requests

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

# --- API PARAMETERS ---
SPORT = 'basketball_nba'
REGIONS = 'us,eu'
# The 6 Sharpest Markets
MARKETS = 'h2h,spreads,totals,player_points,player_assists,player_rebounds'
BOOKMAKERS = 'fanduel,draftkings,bet365,pinnacle'
ODDS_FORMAT = 'decimal'

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_ev_bets():
    if not ODDS_API_KEY:
        print("❌ Error: ODDS_API_KEY is missing.")
        return []

    picks = []
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': MARKETS, 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT}

    try:
        res = requests.get(url, params=params, timeout=15)
        print(f"Credits Remaining: {res.headers.get('x-requests-remaining')}")
        if res.status_code != 200: return []
        
        data = res.json()
        for game in data:
            matchup = f"{game['away_team']} @ {game['home_team']}"
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
                            if ev > 0.01: # 1% Minimum Threshold
                                units = min((ev / (s_price - 1)) / 4 * 100, 5.0)
                                m_label = gid.split('_')[0].replace('player_', '').upper()
                                pt = f" {soft[t]['point']}" if soft[t]['point'] != '' else ""
                                
                                is_emergency = ev >= 0.05
                                header = "🚨 **HIGH CONFIDENCE EMERGENCY ALERT** 🚨" if is_emergency else "💎 **+EV VALUE ALERT** 💎"
                                color = 15158332 if is_emergency else 5763719
                                
                                picks.append({
                                    "msg": (
                                        f"{header}\n"
                                        f"**Edge:** {ev*100:.2f}%\n"
                                        f"**Match:** {matchup}\n"
                                        f"**Market:** {m_label} | {t}{pt}\n"
                                        f"**Book:** {soft[t]['book']} @ {to_american(s_price)}\n"
                                        f"**Sharp Price:** {to_american(1/probs[t])}\n"
                                        f"**Suggested:** {units:.2f} Units\n"
                                    ),
                                    "color": color,
                                    "is_emergency": is_emergency
                                })
        return picks
    except Exception as e:
        print(f"Script Error: {e}")
        return []

def send_alert(pick_data):
    if not DISCORD_WEBHOOK_URL: return
    payload = {
        "content": "@everyone" if pick_data["is_emergency"] else "",
        "embeds": [{
            "description": pick_data["msg"],
            "color": pick_data["color"],
            "image": {"url": FOOTER_IMG}
        }]
    }
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

def main():
    print("--- 🚀 Starting $BEE BAKED Scan ---")
    picks = get_ev_bets()
    
    if picks:
        for p in picks:
            send_alert(p)
            print("✅ Alert Sent to Discord.")
    else:
        # Send a single status embed if no bets are found
        no_ev_payload = {
            "embeds": [{
                "description": "🏀 **Scan Complete:** No +EV edges found. Bankroll safe. 🛡️",
                "color": 3447003, # Blue
                "image": {"url": FOOTER_IMG}
            }]
        }
        requests.post(DISCORD_WEBHOOK_URL, json=no_ev_payload)
        print("✅ 'No Edges' status sent to Discord.")

if __name__ == "__main__":
    main()