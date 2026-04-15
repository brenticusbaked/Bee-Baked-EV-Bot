import json
from db_manager import get_open_clv_bets, update_clv

SPORTS = ['baseball_mlb', 'basketball_nba', 'icehockey_nhl']

def to_american(dec):
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_pinnacle_lines():
    lines = {}
    try:
        with open("master_odds_cache.json", "r") as f:
            cache = json.load(f)
    except FileNotFoundError:
        return lines

    for sport in SPORTS:
        for game in cache.get(sport, []):
            matchup = f"{game['away_team']} @ {game['home_team']}"
            for bm in game.get('bookmakers', []):
                if bm['key'] == 'pinnacle':
                    for mkt in bm['markets']:
                        for out in mkt['outcomes']:
                            point = out.get('point', '')
                            selection_str = f"{out['name']} {point}".strip()
                            key = f"{matchup}_{mkt['key']}_{selection_str}".replace(' ', '_').lower()
                            lines[key] = to_american(float(out['price']))
    return lines

def track_clv():
    open_bets = get_open_clv_bets()
    if not open_bets:
        print("No open bets require CLV tracking.")
        return

    print(f"Found {len(open_bets)} bets awaiting CLV. Fetching Pinnacle lines from cache...")
    pinnacle = get_pinnacle_lines()
    
    for bet in open_bets:
        matchup = bet['matchup'].lower()
        raw_market = bet['market'].lower()
        selection = bet['selection'].lower()

        if 'spread' in raw_market or 'puckline' in raw_market: api_market = 'spreads'
        elif 'total' in raw_market or 'over' in raw_market or 'under' in raw_market: api_market = 'totals'
        elif 'ml' in raw_market or 'moneyline' in raw_market or 'f5' in raw_market or 'h2h' in raw_market: api_market = 'h2h'
        else: api_market = raw_market

        search_key = f"{matchup}_{api_market}_{selection}".replace(' ', '_').lower()
        
        if search_key in pinnacle:
            update_clv(bet['id'], pinnacle[search_key])
            print(f"Updated CLV for {selection}: {pinnacle[search_key]}")

if __name__ == "__main__":
    track_clv()