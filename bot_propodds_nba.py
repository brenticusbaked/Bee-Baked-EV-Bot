import os
import requests
import urllib.parse
from db_manager import is_already_logged, log_bet_to_db

# Configuration & Environment Variables
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

# Target markets for NBA Player Props
TARGET_STATS = ['points', 'assists', 'rebounds']

def to_american(dec):
    """Converts decimal odds to American format."""
    if dec >= 2.0: 
        return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def to_decimal(price):
    """Safely converts various odd formats to decimal."""
    try:
        p = float(price)
        if p > 100: return (p / 100) + 1
        if p < -100: return (100 / abs(p)) + 1
        return p
    except: 
        return 1.909

def get_dynamic_link(bookmaker, target_string):
    """Generates a deep link directly to the sportsbook's search page for the team/player."""
    query = urllib.parse.quote(target_string)
    book = bookmaker.lower().replace(' ', '').replace('sportsbook', '')
    
    links = {
        # State-Regulated (Kentucky Legal)
        'draftkings': f'https://sportsbook.draftkings.com/search?q={query}',
        'fanduel': f'https://sportsbook.fanduel.com/navigation/search?q={query}',
        'betmgm': f'https://sports.betmgm.com/en/sports/search?q={query}',
        'bet365': f'https://www.bet365.com/#/search?q={query}',
        'espn': f'https://espnbet.com/search?q={query}',
        'fanatics': f'https://sportsbook.fanatics.com/search?q={query}',
        'caesars': f'https://sportsbook.caesars.com/us/ky/bet/search?q={query}',
        'betrivers': f'https://betrivers.com/?page=search&q={query}',
        'circasports': 'https://kentucky.circasports.com/',
        'primesports': 'https://primesports.com/',
        'betr': 'https://www.betr.app/',
        
        # Offshore & Professional Options
        'bovada': f'https://www.bovada.lv/sports?search={query}',
        'betonline': 'https://www.betonline.ag/sportsbook',
        'bookmaker': 'https://www.bookmaker.eu/sportsbook',
        'betus': f'https://www.betus.com.pa/sportsbook/search/?q={query}',
        'mybookie': 'https://mybookie.ag/sportsbook/',
        'heritagesports': 'https://www.heritagesports.eu/',
        'sportsbetting': 'https://www.sportsbetting.ag/sportsbook',
        'betnow': 'https://www.betnow.eu/',
        'luckyrebel': 'https://luckyrebel.com/',
        
        # DFS & Exchanges
        'prizepicks': f'https://app.prizepicks.com/search/{query}',
        'pick6': f'https://sportsbook.draftkings.com/pick6/search?q={query}',
        'novig': f'https://novig.co/market-search?q={query}',
        'dabble': f'https://dabble.com.au/search?q={query}',
        'courtside': 'https://courtside.bet/'
    }
    
    return links.get(book, f"https://www.google.com/search?q={bookmaker}+sportsbook")

def get_sgo_edges():
    """Scans the SportsGameOdds API for NBA player prop discrepancies vs Pinnacle."""
    if not SGO_API_KEY: 
        return []

    # Comprehensive list of monitored soft sources
    soft_list = [
        'fanduel', 'draftkings', 'betmgm', 'espn', 'fanatics', 'bet365', 
        'caesars', 'betrivers', 'circasports', 'primesports', 'betr',
        'bovada', 'betonline', 'bookmaker', 'betus', 'mybookie', 
        'heritagesports', 'sportsbetting', 'betnow', 'luckyrebel',
        'prizepicks', 'pick6', 'novig', 'dabble'
    ]

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
            market_groups = {}
            
            for odd_key, odd_obj in event.get('odds', {}).items():
                odd_id = odd_obj.get('oddID', odd_key)
                parts = odd_id.split('-')
                
                # Filter for target stats and ensure data integrity
                if len(parts) < 5 or parts[0] not in TARGET_STATS: 
                    continue
                
                stat_type, player_raw, side = parts[0], parts[1], parts[4]
                bookmaker = odd_obj.get('bookmakerID', 'unknown')
                price = to_decimal(odd_obj.get('price'))
                line = odd_obj.get('handicap')
                
                if price is None or line is None: 
                    continue
                
                player = player_raw.split('_1_')[0].replace('_', ' ').title()
                uid = f"{player}_{stat_type}_{line}"
                
                if uid not in market_groups: 
                    market_groups[uid] = {'sharp': {}, 'soft': {}}
                
                if bookmaker == 'pinnacle':
                    market_groups[uid]['sharp'][side] = price
                elif bookmaker in soft_list:
                    # Capture the best available price for this specific side/line
                    if price > market_groups[uid]['soft'].get(side, {}).get('price', 0):
                        market_groups[uid]['soft'][side] = {'price': price, 'book': bookmaker, 'line': line}

            # Calculate Edge vs No-Vig Sharp Line
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
                                market = s_name.upper()
                                selection = f"{p_name} {side.upper()} {l_val}"
                                
                                if not is_already_logged(matchup, market, selection):
                                    units = min((ev / (s_price - 1)) / 4 * 100, 5.0)
                                    is_em = ev >= 0.06
                                    
                                    # Log to Supabase Cloud Ledger
                                    log_bet_to_db(
                                        matchup.strip(), 
                                        market.strip(), 
                                        selection.strip(), 
                                        to_american(s_price), 
                                        ev, 
                                        f"{units:.2f}", 
                                        to_american(1/probs[side])
                                    )
                                    
                                    deep_link = get_dynamic_link(soft[side]['book'], p_name)
                                    header = "**SGO PROP EMERGENCY**" if is_em else "**NBA PROP ALERT**"
                                    
                                    picks.append({
                                        "msg": f"{header}\n**Edge:** {ev*100:.2f}%\n**Match:** {matchup}\n**Market:** {market} | {selection}\n**Book:** [{soft[side]['book'].upper()}]({deep_link}) @ {to_american(s_price)}\n**Suggested:** {units:.2f} Units",
                                        "color": 15158332 if is_em else 3447003,
                                        "is_emergency": is_em
                                    })
    except Exception as e: 
        print(f"Error fetching SGO prop edges: {e}")
    
    return picks

def main():
    """Executes the prop scanner and dispatches alerts to Discord."""
    picks = get_sgo_edges()
    if not DISCORD_WEBHOOK_URL: 
        return
    
    if picks:
        for p in picks:
            requests.post(DISCORD_WEBHOOK_URL, json={
                "content": "@everyone" if p["is_emergency"] else "", 
                "embeds": [{
                    "description": p["msg"], 
                    "color": p["color"], 
                    "image": {"url": FOOTER_IMG}
                }]
            })
        print(f"Sent {len(picks)} NBA Prop alerts.")
    else: 
        print("SGO Scan Complete: No new NBA player prop edges found.")

if __name__ == "__main__": 
    main()
