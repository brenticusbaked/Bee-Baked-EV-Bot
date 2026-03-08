import os
import requests
import csv
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
SGO_API_KEY = os.getenv("SPORTS_GAME_ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def get_sgo_results(date_str):
    """Fetches SGO boxscores for a specific date."""
    url = "https://api.sportsgameodds.com/v2/events"
    params = {'apiKey': SGO_API_KEY, 'leagueID': 'NBA', 'date': date_str}
    
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            events = res.json()
            stats_map = {}
            for ev in events:
                # Assuming SGO provides a 'boxscore' dict mapping player names to stats
                box = ev.get('boxscore', {})
                for player_name, p_stats in box.items():
                    stats_map[player_name.lower()] = p_stats
            return stats_map
    except Exception as e:
        print(f"Error fetching results for {date_str}: {e}")
    return {}

def run_grader():
    if not os.path.exists('bets_log.csv'): 
        print("No bets logged yet.")
        return
    
    updated_rows = []
    results_found = 0
    profit = 0.0
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Cache boxscores by date so we don't spam the API for the same day
    daily_stats_cache = {}

    with open('bets_log.csv', mode='r') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return
            
        # Ensure Result column exists (should be 10th column)
        if "Result" not in header: 
            header.append("Result")
        updated_rows.append(header)
        
        res_idx = header.index("Result")
        
        for row in reader:
            # Pad row to match header length safely
            while len(row) < len(header): 
                row.append("")
                
            bet_date = row[0]
            market = row[2].lower()
            selection = row[3].lower()
            
            # 1. Check if the bet is from a past date AND is not graded yet
            if bet_date < today and not row[res_idx]:
                
                # 2. Only attempt to grade player props (skip Moneylines/Spreads from other bots)
                if market in ['points', 'assists', 'rebounds']:
                    
                    # Fetch stats for this date if we haven't already
                    if bet_date not in daily_stats_cache:
                        daily_stats_cache[bet_date] = get_sgo_results(bet_date)
                        
                    stats_map = daily_stats_cache[bet_date]
                    
                    # 3. Cleanly extract exact player name (e.g., "lebron james over 25.5" -> "lebron james")
                    is_over = "over" in selection
                    split_word = " over " if is_over else " under "
                    
                    if split_word in selection:
                        parts = selection.split(split_word)
                        player_name = parts[0].strip()
                        line = float(parts[1].strip())
                        
                        # Exact match lookup
                        if player_name in stats_map:
                            actual = stats_map[player_name].get(market, 0)
                            
                            win = (actual > line) if is_over else (actual < line)
                            row[res_idx] = "WIN" if win else "LOSS"
                            
                            # Math for Payout
                            odds = float(row[4].replace('+', '')) if '+' in row[4] else float(row[4])
                            units = float(row[6])
                            
                            if win:
                                # Standard Kelly payout calc for American Odds
                                p = units * (odds/100) if odds > 0 else units * (100/abs(odds))
                                profit += p
                            else:
                                profit -= units
                                
                            results_found += 1
            
            updated_rows.append(row)

    # Save upgraded data back to CSV
    with open('bets_log.csv', mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(updated_rows)

    # Send Notification
    if results_found > 0 and DISCORD_WEBHOOK_URL:
        # Determine color: Green if profitable, Red if negative payout
        embed_color = 5763719 if profit >= 0 else 15158332 
        msg = f"📊 **SGO AUTO-GRADER REPORT** 📊\n━━━━━━━━━━━━━━━━━━━━\n✅ Graded **{results_found}** past bets.\n💰 P/L for Graded Bets: **{profit:+.2f} Units**"
        
        requests.post(DISCORD_WEBHOOK_URL, json={
            "embeds": [{"description": msg, "color": embed_color, "image": {"url": FOOTER_IMG}}]
        })
        print(f"Graded {results_found} bets.")
    else:
        print("No ungraded past bets found.")

if __name__ == "__main__":
    run_grader()