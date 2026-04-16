import os
import requests
from datetime import datetime, timezone
from db_manager import is_already_logged, log_bet_to_db, get_master_cache

# Configuration
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
BEE_IMAGE = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def to_american(dec):
    """Converts decimal odds to American format."""
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_mobile_app_link(book_key, selection_id, event_id, matchup):
    """
    Generates Deep Links to open the sportsbook app directly on mobile.
    If the app is not installed, these typically fallback to the mobile browser.
    """
    # URL encode matchup for search-based fallbacks
    query = requests.utils.quote(matchup)
    
    links = {
        'draftkings': f"draftkings://sportsbook/event/{event_id}?selectionId={selection_id}",
        'fanduel': f"fanduel://sportsbook/navigation/event/{event_id}/selection/{selection_id}",
        'betmgm': f"betmgm://sportsbook/event/{event_id}",
        'caesars': f"caesars://sportsbook/search?q={query}",
        'bet365': f"bet365://sportsbook/event/{event_id}",
        'prizepicks': f"https://app.prizepicks.com/search/{query}"
    }
    
    # Return the deep link or a Google search fallback if the book is unknown
    return links.get(book_key, f"https://www.google.com/search?q={book_key}+{query}")

def scan_markets():
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return

    alerts = []
    # Targeted soft books
    soft_books = ['fanduel', 'draftkings', 'betmgm', 'bet365', 'caesars', 'bovada']
    now = datetime.now(timezone.utc)

    for sport, events in cache.items():
        for event in events:
            # --- PHASE 1: COMMENCE TIME FILTER ---
            # Skips games that have already started to prevent stale +EV
            commence_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
            if now > commence_time:
                continue

            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}
            
            # --- PHASE 2: MARKET PROCESSING ---
            for bm in event['bookmakers']:
                for mkt in bm['markets']:
                    mkt_key = mkt['key']
                    if mkt_key not in markets: 
                        markets[mkt_key] = {'sharp': {}, 'soft': []}
                    
                    if bm['key'] == 'pinnacle':
                        for outcome in mkt['outcomes']:
                            markets[mkt_key]['sharp'][outcome['name'].lower().strip()] = float(outcome['price'])
                    elif bm['key'] in soft_books:
                        for outcome in mkt['outcomes']:
                            markets[mkt_key]['soft'].append({
                                'book': bm['title'], 'book_key': bm['key'],
                                'name': outcome['name'], 'price': float(outcome['price']), 
                                'point': outcome.get('point', ''), 'id': outcome.get('id')
                            })

            # --- PHASE 3: EDGE DETECTION & CONTRADICTION FILTER ---
            for m_type, data in markets.items():
                sharp = data['sharp']
                if not sharp: continue 
                
                best_edge = {"edge": 0, "bet": None}

                for s_bet in data['soft']:
                    lookup_name = s_bet['name'].lower().strip()
                    if lookup_name in sharp:
                        p_price = sharp[lookup_name]
                        if s_bet['price'] > p_price * 1.03: # 3% Edge Threshold
                            ev = (s_bet['price'] / p_price) - 1
                            if ev > best_edge["edge"]:
                                best_edge = {"edge": ev, "bet": s_bet}

                final = best_edge["bet"]
                if final:
                    ev = best_edge["edge"]
                    selection = f"{final['name']} {final['point']}".strip()
                    
                    if not is_already_logged(matchup, m_type, selection):
                        # Unit Sizing: Quarter Kelly
                        units = min((ev / (final['price'] - 1)) / 4 * 100, 5.0) 
                        
                        # Calculate Fair Value (Pinnacle's sharp price)
                        fv_american = to_american(sharp[final['name'].lower().strip()])
                        
                        log_bet_to_db(matchup, m_type, selection, to_american(final['price']), ev, f"{units:.2f}", fv_american, sport, event['id'])
                        
                        # Generate Mobile Deep Link
                        app_link = get_mobile_app_link(final['book_key'], final['id'], event['id'], matchup)
                        
                        alerts.append({
                            "description": (
                                f"🟢 **+EV {m_type.upper()} ALERT**\n\n"
                                f"**Match:** {matchup}\n"
                                f"**Bet:** {selection}\n"
                                f"**Book:** [{final['book']}]({app_link}) @ {to_american(final['price'])}\n"
                                f"**Fair Value:** {fv_american}\n"
                                f"**Edge:** {ev*100:.2f}%\n"
                                f"**Suggested:** {units:.2f} Units"
                            )
                        })

    # --- PHASE 4: DISCORD DELIVERY ---
    if alerts and DISCORD_WEBHOOK_URL:
        for a in alerts: 
            payload = {
                "embeds": [{
                    "description": a["description"],
                    "color": 3066993, # Bee-Baked Green
                    "image": {"url": BEE_IMAGE}, # Branding footer
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }]
            }
            requests.post(DISCORD_WEBHOOK_URL, json=payload)

if __name__ == "__main__": 
    scan_markets()