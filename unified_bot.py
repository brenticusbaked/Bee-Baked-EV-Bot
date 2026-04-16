import os
import requests
from datetime import datetime, timezone
from db_manager import is_already_logged, log_bet_to_db, get_master_cache

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def to_american(dec):
    """Converts decimal odds to American format."""
    if dec >= 2.0: return f"+{int((dec - 1) * 100)}"
    return str(int(-100 / (dec - 1)))

def get_dynamic_link(book_key, selection_id, event_id):
    """Generates a clickable deep link for the specific sportsbook."""
    if book_key == 'draftkings':
        return f"https://sportsbook.draftkings.com/event/{event_id}?selectionId={selection_id}"
    elif book_key == 'fanduel':
        return f"https://sportsbook.fanduel.com/sports/event/{event_id}/selection/{selection_id}"
    elif book_key == 'betmgm':
        # FIXED: Specific BetMGM routing logic
        return f"https://sports.betmgm.com/en/sports/events/{event_id}"
    elif book_key == 'prizepicks':
        return f"https://app.prizepicks.com/"
    
    # Fallback to general DK link if book is unknown
    return "https://sportsbook.draftkings.com"

def scan_markets():
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return

    alerts = []
    # Note: PrizePicks removed here as it does not offer Game Markets (H2H/Spread/Total)
    soft_books = ['fanduel', 'draftkings', 'betmgm', 'bet365', 'caesars', 'bovada']
    
    # Get current UTC time for commencement check
    now = datetime.now(timezone.utc)

    for sport, events in cache.items():
        for event in events:
            # --- COMMENCE TIME FILTER ---
            # Parse the start time from the API (ISO format)
            commence_time = datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00'))
            
            # Skip games that have already started to prevent incorrect +EV alerts
            if now > commence_time:
                continue

            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}
            
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
                                'book': bm['title'], 
                                'book_key': bm['key'],
                                'name': outcome['name'], 
                                'price': float(outcome['price']), 
                                'point': outcome.get('point', ''),
                                'id': outcome.get('id')
                            })

            for m_type, data in markets.items():
                sharp = data['sharp']
                if not sharp: continue 
                
                # Best Edge Filter to avoid contradicting bets (Over and Under on same game)
                best_edge_for_market = {"edge": 0, "bet": None}

                for s_bet in data['soft']:
                    lookup_name = s_bet['name'].lower().strip()
                    if lookup_name in sharp:
                        p_price = sharp[lookup_name]
                        if s_bet['price'] > p_price * 1.03: # 3% Edge Threshold
                            ev = (s_bet['price'] / p_price) - 1
                            if ev > best_edge_for_market["edge"]:
                                best_edge_for_market = {"edge": ev, "bet": s_bet}

                final_bet = best_edge_for_market["bet"]
                if final_bet:
                    ev = best_edge_for_market["edge"]
                    selection = f"{final_bet['name']} {final_bet['point']}".strip()
                    
                    if not is_already_logged(matchup, m_type, selection):
                        # Quarter Kelly Sizing
                        units = min((ev / (final_bet['price'] - 1)) / 4 * 100, 5.0) 
                        log_bet_to_db(matchup, m_type, selection, to_american(final_bet['price']), ev, f"{units:.2f}", to_american(sharp[final_bet['name'].lower().strip()]), sport, event['id'])
                        
                        # Generate correctly routed deep link
                        bet_link = get_dynamic_link(final_bet['book_key'], final_bet['id'], event['id'])
                        
                        alerts.append(
                            f"🟢 **+EV {m_type.upper()}**\n"
                            f"**Match:** {matchup}\n"
                            f"**Bet:** {selection}\n"
                            f"**Book:** [{final_bet['book']}]({bet_link}) @ {to_american(final_bet['price'])}\n"
                            f"**Edge:** {ev*100:.2f}%\n"
                            f"**Suggested:** {units:.2f} Units"
                        )

    if alerts and DISCORD_WEBHOOK_URL:
        for a in alerts: 
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": a, "color": 3066993}]})

if __name__ == "__main__": 
    scan_markets()