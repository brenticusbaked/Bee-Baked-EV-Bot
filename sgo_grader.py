import os
import requests
from db_manager import get_ungraded_past_bets, update_result

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY")

def get_sgo_results(league_id, date_str):
    url = "https://api.sportsgameodds.com/v2/events"
    params = {'apiKey': SGO_API_KEY, 'leagueID': league_id, 'date': date_str}
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            events = res.json()
            stats = {'players': {}, 'games': {}}
            for ev in events:
                for p_name, p_stats in ev.get('boxscore', {}).items():
                    stats['players'][p_name.lower().strip()] = p_stats
                stats['games'][ev.get('name', '').lower().strip()] = ev.get('scores', {})
            return stats
    except: return {'players': {}, 'games': {}}

def run_grader():
    ungraded_bets = get_ungraded_past_bets()
    if not ungraded_bets: return
        
    results_found = 0
    profit = 0.0
    cache = {}
    league_map = {'basketball_nba': 'NBA', 'icehockey_nhl': 'NHL', 'baseball_mlb': 'MLB'}

    print(f"🔍 Grading {len(ungraded_bets)} bets...")

    for bet in ungraded_bets:
        league = league_map.get(bet.get('sport'), 'NBA')
        ckey = f"{league}_{bet['date']}"
        if ckey not in cache: cache[ckey] = get_sgo_results(league, bet['date'])
        
        data = cache[ckey]
        
        # FIXED: Bridge 'PLAYER_POINTS' -> 'points' case mismatch
        market = bet['market'].lower().replace('player_', '').strip()
        selection = bet['selection'].lower().strip()
        
        if market in ['points', 'assists', 'rebounds', 'goals']:
            is_over = "over" in selection
            split_word = " over " if is_over else " under "
            if split_word in selection:
                parts = selection.split(split_word)
                try:
                    p_name, line = parts[0].strip(), float(parts[1].strip())
                    if p_name in data['players']:
                        actual = data['players'][p_name].get(market, 0)
                        if actual == line:
                            update_result(bet['id'], "PUSH")
                        else:
                            win = (actual > line) if is_over else (actual < line)
                            update_result(bet['id'], "WIN" if win else "LOSS")
                            
                            # American Odds P/L
                            odds = float(bet['odds'].replace('+', ''))
                            units = float(bet['units'])
                            profit += (units * (odds/100)) if win else -units
                        results_found += 1
                except: continue

    if results_found > 0 and DISCORD_WEBHOOK_URL:
        msg = f"📊 **SGO GRADER REPORT**\n✅ Graded: {results_found}\n💰 Net P/L: **{profit:+.2f} Units**"
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 5763719 if profit >= 0 else 15158332}]})

if __name__ == "__main__": run_grader()