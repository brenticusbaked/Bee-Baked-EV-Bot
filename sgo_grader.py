import os
import requests
from db_manager import get_ungraded_past_bets, update_result

# Configuration & Environment Variables
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def get_sgo_results(league_id, date_str):
    """
    Fetches game results and player boxscores from the SGO API for a specific 
    league and date.
    """
    url = "https://api.sportsgameodds.com/v2/events"
    params = {'apiKey': SGO_API_KEY, 'leagueID': league_id, 'date': date_str}
    
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            events = res.json()
            stats_map = {'players': {}, 'games': {}}
            
            for ev in events:
                # Store Player Stats (NBA/MLB Props)
                box = ev.get('boxscore', {})
                for player_name, p_stats in box.items():
                    stats_map['players'][player_name.lower()] = p_stats
                
                # Store Game Stats (NHL Puck Lines / MLB F5)
                matchup = ev.get('name', '').lower()
                stats_map['games'][matchup] = ev.get('scores', {})
                
            return stats_map
    except Exception as e: 
        print(f"Error fetching SGO {league_id} results: {e}")
    return {'players': {}, 'games': {}}

def run_grader():
    """
    Audits ungraded bets in the database against real-time final scores 
    and player boxscores.
    """
    ungraded_bets = get_ungraded_past_bets()
    if not ungraded_bets: 
        print("No ungraded bets found in the ledger.")
        return
        
    results_found = 0
    profit = 0.0
    # Cache results by League + Date to minimize API requests
    league_stats_cache = {}

    # Map API sport strings to SGO League IDs
    league_map = {
        'basketball_nba': 'NBA',
        'icehockey_nhl': 'NHL',
        'baseball_mlb': 'MLB'
    }

    print(f"🔍 Grading {len(ungraded_bets)} bets...")

    for bet in ungraded_bets:
        bet_date = bet['date']
        sport_key = bet.get('sport', 'basketball_nba')
        league_id = league_map.get(sport_key, 'NBA')
        
        cache_key = f"{league_id}_{bet_date}"
        if cache_key not in league_stats_cache:
            league_stats_cache[cache_key] = get_sgo_results(league_id, bet_date)
        
        data = league_stats_cache[cache_key]
        market, selection = bet['market'].lower(), bet['selection'].lower()
        
        # --- PLAYER PROP GRADING (NBA / MLB) ---
        if market in ['points', 'assists', 'rebounds']:
            is_over = "over" in selection
            split_word = " over " if is_over else " under "
            
            if split_word in selection:
                parts = selection.split(split_word)
                try:
                    player_name, line = parts[0].strip(), float(parts[1].strip())
                    if player_name in data['players']:
                        actual = data['players'][player_name].get(market, 0)
                        
                        if actual == line:
                            update_result(bet['id'], "PUSH")
                            results_found += 1
                        else:
                            win = (actual > line) if is_over else (actual < line)
                            update_result(bet['id'], "WIN" if win else "LOSS")
                            
                            # Calculate Unit Impact
                            odds_raw = bet['odds'].replace('+', '')
                            odds = float(odds_raw)
                            units = float(bet['units'])
                            
                            if