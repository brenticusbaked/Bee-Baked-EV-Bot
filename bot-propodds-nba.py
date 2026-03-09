import os
import requests
import csv
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

TARGET_STATS = ['points', 'assists']

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def to_decimal(price):
    try:
        p = float(price)
        if p > 100: return (p / 100) + 1
        if p < -100: return (100 / abs(p)) + 1
        return p
    except:
        return 1.909

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

def get_sgo_edges():
    if not SGO_API_KEY:
        return []

    picks = []
    url = "https://api.sportsgameodds.com/v2/events"
    params = {'apiKey': SGO_API_KEY, 'leagueID': 'NBA', 'oddsAvailable': 'true'}

    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code != 200:
            return []
        
        data = res.json()
        for event in data:
            matchup = event.get('name', 'Unknown Matchup')
            odds_data = event.get('odds', {})
            market_groups = {}
            
            for odd_key, odd_obj in odds_data.items():
                odd_id = odd_obj.get('oddID', odd_key)
                parts = odd_id.split('-')
                
                if len(parts) < 5: 
                    continue
                
                stat_type = parts[0]
                if stat_type not in TARGET_STATS: 
                    continue
                
                player_raw = parts[1]
                side = parts[4]
                bookmaker = odd_obj.get('bookmakerID', 'unknown')
                price_raw = odd_obj.get('price') 
                line = odd_obj.get('handicap')
                
                if price_raw is None or line is None: 
                    continue
                
                price = to_decimal(price_raw)
                player = player_raw.split('_1_')[0].replace('_', ' ').title()
                uid = f"{player}_{stat_type}_{line}"
                
                if uid not in market_groups:
                    market_groups[uid] = {'sharp': {}, 'soft': {}}
                
                if bookmaker == 'pinnacle':
                    market_groups[uid]['sharp'][side] = price
                elif bookmaker in ['fanduel', 'draftkings', 'betmgm', 'espn', 'fanatics', 'bet365']:
                    current_soft_price = market_groups[uid]['soft'].get(side, {}).get('price', 0)
                    if price > current_soft_price:
                        market_groups[uid]['soft'][side] = {'price': price, 'book': bookmaker, 'line': line}

            # Process Markets
            for uid, val in market_groups.items():
                sharp, soft = val['sharp'], val['soft']
                
                if 'over' in sharp and 'under' in sharp:
                    p_over, p_under = sharp['over'], sharp['under']
                    vig = (1/p_over) + (1/p_under)
                    probs = {'over': (1/p_over)/vig, 'under': (1/p_under)/vig}
                    
                    for side in ['over', 'under']:
                        if side in soft:
                            s_price = soft[side]['price']
                            ev = (s_price * probs[side]) - 1
                            if ev > 0.02:
                                p_name, s_name, l_val = uid.split('_')
                                units = min((ev / (s_price - 1)) / 4 * 100, 5.0)
                                is_em = ev >= 0.06
                                f_american = to_american(1/probs[side])
                                log_bet_to_csv(matchup, s_name.upper(), f"{p_name} {side.upper()} {l_val}", to_american(s_price), ev, f"{units:.2f}", f_american)
                                
                                header = "🚨 **SGO PROP EMERGENCY** 🚨" if is_em else "🎯 **NBA PROP ALERT** 🎯"
                                picks.append({
                                    "msg": f"{header}\n**Edge:** {ev*100:.2f}%\n**Match:** {matchup}\n**Market:** {s_name.upper()} | {p_name} {side.upper()} {l_val}\n**Book:** {soft[side]['book'].upper()} @ {to_american(s_price)}\n**Suggested:** {units:.2f} Units",
                                    "color": 15158332 if is_em else 3447003,
                                    "is_emergency": is_em
                                })
    except Exception as e:
        print(f"Error: {e}")
    return picks

def main():
    picks = get_sgo_edges()
    if not DISCORD_WEBHOOK_URL: 
        return
    
    if picks:
        for p in picks:
            requests.post(DISCORD_WEBHOOK_URL, json={
                "content": "@everyone" if p["is_emergency"] else "", 
                "embeds": [{"description": p["msg"], "color": p["color"], "image": {"url": FOOTER_IMG}}]
            })
    else:
        requests.post(DISCORD_WEBHOOK_URL, json={
            "embeds": [{"description": "🎯 **SGO Scan Complete:** No NBA player prop edges found.", "color": 3447003, "image": {"url": FOOTER_IMG}}]
        })

if __name__ == "__main__":
    main()