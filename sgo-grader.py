import os
import requests
import csv
from datetime import datetime, timedelta

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SPORTS_GAME_ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def run_grader():
    if not os.path.exists('bets_log.csv'): return
    
    updated_rows = []
    results_found = 0
    profit = 0.0

    # 1. Get yesterday's results from SGO
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    url = f"https://api.sportsgameodds.com/v2/events"
    params = {'apiKey': SGO_API_KEY, 'leagueID': 'NBA', 'date': yesterday}
    
    try:
        res = requests.get(url, params=params, timeout=15)
        events = res.json() if res.status_code == 200 else []
    except:
        events = []

    # Map player stats for easy lookup
    stats_map = {}
    for ev in events:
        box = ev.get('boxscore', {})
        for player_id, p_stats in box.items():
            stats_map[player_id.lower()] = p_stats

    # 2. Read and Grade the Log
    with open('bets_log.csv', mode='r') as f:
        reader = csv.reader(f)
        header = next(reader)
        if "Result" not in header: header.append("Result")
        updated_rows.append(header)
        
        res_idx = header.index("Result")
        
        for row in reader:
            while len(row) < len(header): row.append("")
            
            # Only grade bets from yesterday that aren't graded yet
            if row[0] == yesterday and not row[res_idx]:
                selection = row[3].lower() # e.g., "lebron james over 25.5"
                market = row[2].lower()    # e.g., "POINTS"
                odds = float(row[4].replace('+', '')) if '+' in row[4] else float(row[4])
                units = float(row[6])

                # Find player in stats_map
                player_match = next((s for s in stats_map if s in selection), None)
                if player_match:
                    actual = stats_map[player_match].get(market, 0)
                    line = float(selection.split()[-1])
                    is_over = "over" in selection
                    
                    win = (actual > line) if is_over else (actual < line)
                    row[res_idx] = "WIN" if win else "LOSS"
                    
                    # Calculate Profit (Assuming American Odds)
                    if win:
                        p = units * (odds/100) if odds > 0 else units * (100/abs(odds))
                        profit += p
                    else:
                        profit -= units
                    results_found += 1
            
            updated_rows.append(row)

    # 3. Save and Notify
    with open('bets_log.csv', mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(updated_rows)

    if results_found > 0 and DISCORD_WEBHOOK_URL:
        msg = f"📊 **SGO AUTO-GRADER REPORT** 📊\n━━━━━━━━━━━━━━━━━━━━\n✅ Graded **{results_found}** bets from yesterday.\n💰 Daily P/L: **{profit:+.2f} Units**"
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 3066993, "image": {"url": FOOTER_IMG}}]})

if __name__ == "__main__":
    run_grader()