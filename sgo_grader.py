import os
import requests
from db_manager import get_ungraded_past_bets, update_result

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SGO_API_KEY") 
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def get_sgo_results(date_str):
    url = "https://api.sportsgameodds.com/v2/events"
    params = {'apiKey': SGO_API_KEY, 'leagueID': 'NBA', 'date': date_str}
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            events = res.json()
            stats_map = {}
            for ev in events:
                box = ev.get('boxscore', {})
                for player_name, p_stats in box.items():
                    stats_map[player_name.lower()] = p_stats
            return stats_map
    except Exception as e: print(f"Error fetching SGO results: {e}")
    return {}

def run_grader():
    ungraded_bets = get_ungraded_past_bets()
    if not ungraded_bets: return
        
    results_found = 0
    profit = 0.0
    daily_stats_cache = {}

    for bet in ungraded_bets:
        bet_date = bet['date']
        market, selection = bet['market'].lower(), bet['selection'].lower()
        
        if market in ['points', 'assists', 'rebounds']:
            if bet_date not in daily_stats_cache: daily_stats_cache[bet_date] = get_sgo_results(bet_date)
            stats_map = daily_stats_cache[bet_date]
            
            is_over = "over" in selection
            split_word = " over " if is_over else " under "
            
            if split_word in selection:
                parts = selection.split(split_word)
                try:
                    player_name, line = parts[0].strip(), float(parts[1].strip())
                    if player_name in stats_map:
                        actual = stats_map[player_name].get(market, 0)
                        if actual == line:
                            update_result(bet['id'], "PUSH")
                            results_found += 1
                        else:
                            win = (actual > line) if is_over else (actual < line)
                            update_result(bet['id'], "WIN" if win else "LOSS")
                            odds, units = float(bet['odds'].replace('+', '')), float(bet['units'])
                            if win: profit += units * (odds/100) if odds > 0 else units * (100/abs(odds))
                            else: profit -= units
                            results_found += 1
                except (ValueError, IndexError): continue

    if results_found > 0 and DISCORD_WEBHOOK_URL:
        embed_color = 5763719 if profit >= 0 else 15158332 
        msg = f"📊 **SGO AUTO-GRADER REPORT** 📊\n━━━━━━━━━━━━━━━━━━━━\n✅ Graded **{results_found}** past bets.\n💰 P/L for Graded Bets: **{profit:+.2f} Units**"
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": embed_color, "image": {"url": FOOTER_IMG}}]})

if __name__ == "__main__": run_grader()