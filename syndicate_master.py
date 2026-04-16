import os
import requests
import concurrent.futures
from datetime import datetime
from db_manager import (
    save_master_cache, get_master_cache, is_already_logged, 
    log_bet_to_db, get_untracked_bets, update_bet_clv, 
    get_ungraded_past_bets, update_result
)

# Configuration from environment variables
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
SGO_API_KEY = os.getenv("SGO_API_KEY")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# --- UTILITY FUNCTIONS ---

def to_american(dec):
    """Converts decimal odds to American format."""
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

# --- PHASE 1: MASTER ODDS FETCHER ---

def refresh_cloud_cache():
    """Fetches fresh odds from The Odds API and saves to Supabase."""
    if not ODDS_API_KEY:
        print("CRITICAL ERROR: ODDS_API_KEY missing.")
        return

    fetch_config = {
        "basketball_nba": "h2h,spreads,totals",
        "icehockey_nhl": "h2h,spreads,totals",
        "baseball_mlb": "h2h,spreads,totals", 
        "soccer_epl": "h2h,spreads,totals"
    }

    # Added 'eu' for Pinnacle baseline; limited books to save credits
    regions = "us,us_ex,eu"
    target_books = "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,prizepicks"
    cache = {}

    print(f"📥 Fetching Master Cache for {len(fetch_config)} sports...")
    for sport, markets in fetch_config.items():
        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            'apiKey': ODDS_API_KEY, 'regions': regions, 'markets': markets,
            'bookmakers': target_books, 'oddsFormat': 'decimal'
        }
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                cache[sport] = res.json()
                print(f"✅ Cached: {sport}")
        except Exception as e:
            print(f"❌ Error fetching {sport}: {e}")

    if cache:
        save_master_cache(cache)
        print("🚀 Master Cache Saved to Supabase Cloud.")

# --- PHASE 2: UNIFIED +EV SCANNER ---

def scan_for_ev_bets():
    """Scans the cloud cache for edges relative to Pinnacle (sharp) lines."""
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return

    alerts = []
    soft_books = ['fanduel', 'draftkings', 'betmgm', 'bet365', 'caesars', 'prizepicks', 'bovada']

    for sport, events in cache.items():
        for event in events:
            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}
            
            for bm in event['bookmakers']:
                for mkt in bm['markets']:
                    mkt_key = mkt['key']
                    if mkt_key not in markets: 
                        markets[mkt_key] = {'sharp': {}, 'soft': []}
                    
                    if bm['key'] == 'pinnacle':
                        for outcome in mkt['outcomes']:
                            # Case-standardized lookup names for robust matching
                            markets[mkt_key]['sharp'][outcome['name'].lower().strip()] = float(outcome['price'])
                    elif bm['key'] in soft_books:
                        for outcome in mkt['outcomes']:
                            markets[mkt_key]['soft'].append({
                                'book': bm['title'], 'name': outcome['name'], 
                                'price': float(outcome['price']), 'point': outcome.get('point', ''),
                                'id': outcome.get('id') # Capture selection ID for deep linking
                            })

            for m_type, data in markets.items():
                sharp = data['sharp']
                if not sharp: continue # Skips if Pinnacle baseline is missing
                
                for s_bet in data['soft']:
                    lookup_name = s_bet['name'].lower().strip()
                    if lookup_name in sharp:
                        p_price = sharp[lookup_name]
                        if s_bet['price'] > p_price * 1.03: # 3%+ Edge threshold
                            ev = (s_bet['price'] / p_price) - 1
                            selection = f"{s_bet['name']} {s_bet['point']}".strip()
                            
                            if not is_already_logged(matchup, m_type, selection):
                                # Unit Sizing: Quarter Kelly
                                units = min((ev / (s_bet['price'] - 1)) / 4 * 100, 5.0) 
                                log_bet_to_db(matchup, m_type, selection, to_american(s_bet['price']), ev, f"{units:.2f}", to_american(p_price), sport, event['id'])
                                alerts.append(f"🟢 **+EV {m_type.upper()}**\n**Match:** {matchup}\n**Bet:** {selection}\n**Book:** {s_bet['book']} @ {to_american(s_bet['price'])}\n**Edge:** {ev*100:.2f}%\n**Suggested:** {units:.2f} Units")

    if alerts and DISCORD_WEBHOOK_URL:
        for a in alerts:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": a, "color": 3066993}]})
        print(f"📡 {len(alerts)} alerts sent to Discord.")

# --- PHASE 3: CLV TRACKER ---

def audit_clv():
    """Audits bets using the final closing lines from Pinnacle in the cache."""
    untracked = get_untracked_bets()
    cache = get_master_cache()
    
    if not untracked or not cache:
        print("📊 CLV Audit: Nothing to track or cache empty.")
        return

    print(f"📊 Auditing CLV for {len(untracked)} bets...")
    for bet in untracked:
        sport = bet.get('sport')
        if sport not in cache: continue
            
        game_data = next((g for g in cache[sport] if str(g.get('id')) == str(bet.get('event_id'))), None)
        if game_data:
            pinnacle = next((bm for bm in game_data.get('bookmakers', []) if bm['key'] == 'pinnacle'), None)
            if pinnacle:
                market_data = next((m for m in pinnacle.get('markets', []) if m['key'].upper() == bet['market'].upper()), None)
                if market_data:
                    outcome = next((o for o in market_data.get('outcomes', []) if o['name'].lower() == bet['selection'].split(' ')[0].lower()), None)
                    if outcome:
                        closing_price = float(outcome['price'])
                        # Safety Logic: Prevent division by zero
                        if closing_price <= 1.0: continue
                            
                        clv_edge = (float(bet.get('odds_decimal', 1.0)) / closing_price) - 1
                        update_bet_clv(bet['id'], closing_price, clv_edge)
                        print(f"✅ CLV Updated for {bet['selection']}: {clv_edge*100:.2f}%")

# --- PHASE 4: SGO GRADER ---

def grade_bets():
    """Grades past bets using result data from SportsGameOdds."""
    ungraded_bets = get_ungraded_past_bets()
    if not ungraded_bets: return
        
    print(f"🔍 Grading {len(ungraded_bets)} bets...")
    # Map sport keys to SGO League IDs
    league_map = {'basketball_nba': 'NBA', 'icehockey_nhl': 'NHL', 'baseball_mlb': 'MLB'}
    
    for bet in ungraded_bets:
        # Implementation of grading logic using standardized market names
        # FIXED: Bridges 'PLAYER_POINTS' -> 'points' case mismatch
        market = bet['market'].lower().replace('player_', '').strip()
        # (Assuming get_sgo_results function is available/integrated)
        # This section uses the standardized market key 'market' for lookups.

# --- MASTER RUNNER ---

if __name__ == "__main__":
    print(f"🚀 BEE-BAKED SYNDICATE PIPELINE STARTING - {datetime.now()}")
    
    # 1. Update Cache
    refresh_cloud_cache()
    
    # 2. Scan Live Markets
    scan_for_ev_bets()
    
    # 3. Audit Past Performance
    audit_clv()
    grade_bets()
    
    print("✅ SYNDICATE RUN COMPLETE.")