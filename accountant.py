import os
import csv
import requests
from datetime import datetime, timedelta

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def calculate_profit(odds, units):
    """Calculates profit based on American odds."""
    if odds > 0:
        return units * (odds / 100)
    else:
        return units * (100 / abs(odds))

def run_accountant():
    if not os.path.exists('bets_log.csv'):
        print("No bets logged yet.")
        if DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "📊 **$BEE BAKED Accountant:** No bets logged yet. System is standing by.", "color": 8421504}]})
        return

    # Lookback window for the last 7 days
    last_week = datetime.now() - timedelta(days=7)

    total_bets = 0
    total_units_risked = 0.0
    edges = []
    
    # Grading Stats
    graded_bets = 0
    wins = 0
    total_profit = 0.0
    
    # CLV Stats
    clv_tracked = 0
    clv_beats = 0
    
    with open('bets_log.csv', mode='r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                bet_date = datetime.strptime(row['Date'], "%Y-%m-%d")
                if bet_date >= last_week:
                    total_bets += 1
                    units = float(row['Units'])
                    total_units_risked += units
                    edges.append(float(row['Edge'].replace('%', '')))
                    
                    # Track CLV Beating
                    bet_odds_str = row.get('Odds', '')
                    clv_str = row.get('Closing_Line_Pinnacle', '')
                    
                    if clv_str and bet_odds_str:
                        clv_tracked += 1
                        bet_odds = float(bet_odds_str.replace('+', ''))
                        clv_odds = float(clv_str.replace('+', ''))
                        # In American odds, a higher number is a better payout
                        if bet_odds > clv_odds:
                            clv_beats += 1
                            
                    # Track Actual Profit/Loss
                    result = row.get('Result', '').upper()
                    if result in ['WIN', 'LOSS']:
                        graded_bets += 1
                        if result == 'WIN':
                            wins += 1
                            bet_odds = float(bet_odds_str.replace('+', ''))
                            total_profit += calculate_profit(bet_odds, units)
                        elif result == 'LOSS':
                            total_profit -= units
                            
            except Exception as e:
                # Skip rows with formatting errors
                continue
    
    if total_bets == 0:
        if DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "📊 **Weekly Accountant Report:** No bets placed this week. Bankroll preserved.", "color": 8421504}]})
        return

    # Math Summaries
    avg_edge = sum(edges) / len(edges) if edges else 0
    win_rate = (wins / graded_bets * 100) if graded_bets > 0 else 0
    clv_beat_rate = (clv_beats / clv_tracked * 100) if clv_tracked > 0 else 0
    roi = (total_profit / total_units_risked * 100) if total_units_risked > 0 else 0

    # Determine Embed Color (Green for profit, Red for loss, Gray for pending)
    if graded_bets == 0:
        embed_color = 8421504 # Gray
    elif total_profit >= 0:
        embed_color = 5763719 # Green
    else:
        embed_color = 15158332 # Red

    # Format the Discord Executive Summary
    report = (
        f"📊 **$BEE BAKED 7-DAY ACCOUNTANT REPORT** 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**📈 FORWARD METRICS (Volume & Edge)**\n"
        f"• **+EV Bets Placed:** {total_bets}\n"
        f"• **Total Risked:** {total_units_risked:.2f} Units\n"
        f"• **Average Edge:** {avg_edge:.2f}%\n"
        f"• **CLV Beat Rate:** {clv_beat_rate:.1f}% ({clv_beats}/{clv_tracked} tracked)\n\n"
        f"**💰 REALIZED METRICS (Profit & Loss)**\n"
        f"• **Graded Bets:** {graded_bets}\n"
        f"• **Win Rate:** {win_rate:.1f}% ({wins}W - {graded_bets - wins}L)\n"
        f"• **Net Profit:** **{total_profit:+.2f} Units**\n"
        f"• **ROI:** {roi:+.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={
            "embeds": [{
                "description": report, 
                "color": embed_color,
                "image": {"url": FOOTER_IMG}
            }]
        })
        print("Accountant report sent to Discord.")

if __name__ == "__main__":
    run_accountant()