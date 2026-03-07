import os
import requests
import csv
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

# --- API PARAMS ---
SPORT = 'tennis_atp' # Men's Tennis
REGIONS = 'us,eu'
MARKETS = 'h2h' # Match Winner
BOOKMAKERS = 'fanduel,draftkings,bet365,pinnacle'
ODDS_FORMAT = 'decimal'

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

def get_ev_bets():
    if not ODDS_API_KEY: return []
    picks = []
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
    params = {'apiKey': ODDS_API_KEY, 'regions': REGIONS, 'markets': MARKETS, 'bookmakers': BOOKMAKERS, 'oddsFormat': ODDS_FORMAT}

    try:
        res = requests.get(url, params=params, timeout=15)
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
                        if name == 'pinnacle': market_groups[gid]['sharp'][label] = price
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
                                is_emergency = ev >= 0.05
                                
                                fair_american = to_american(1/probs[t])
                                log_bet_to_csv(matchup, m_label, t, to_american(s_price), ev, f"{units:.2f}", fair_american)

                                header = "🚨 **TENNIS EMERGENCY** 🚨" if is_emergency else "🎾 **ATP +EV ALERT** 🎾"
                                picks.append({
                                    "msg": f"{header}\n**Edge:** {ev*100:.2f}%\n**Match:** {matchup}\n**Market:** {m_label} | {t}\n**Book:** {soft[t]['book']} @ {to_american(s_price)}\n**Suggested:** {units:.2f} Units",
                                    "color": 15158332 if is_emergency else 11001111,
                                    "is_emergency": is_emergency
                                })
        return picks
    except Exception as e:
        print(f"Error: {e}"); return []

def send_alert(p):
    if not DISCORD_WEBHOOK_URL: return
    requests.post(DISCORD_WEBHOOK_URL, json={"content": "@everyone" if p["is_emergency"] else "", "embeds": [{"description": p["msg"], "color": p["color"], "image": {"url": FOOTER_IMG}}]})

def main():
    picks = get_ev_bets()
    if picks:
        for p in picks: send_alert(p)
    else:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "🎾 **Tennis Scan Complete:** No edges found.", "color": 11001111, "image": {"url": FOOTER_IMG}}]})

if __name__ == "__main__":
    main()