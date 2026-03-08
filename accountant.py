import os
import csv
import requests
from datetime import datetime, timedelta

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def run_accountant():
    if not os.path.exists('bets_log.csv'):
        print("No bets logged yet.")
        if DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": "📊 **$BEE BAKED Accountant:** No bets logged yet. System is standing by.", "color": 8421504}]})
        return

    # We only want to calculate stats for the last 7 days
    today = datetime.now()
    last_week = today - timedelta(days=7)

    total_bets = 0
    total_units = 0.0
    edges = []
    
    with open('bets_log.csv', mode='r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                bet_date = datetime.strptime(row['Date'], "%Y-%m-%d")
                if bet_date >= last_week:
                    total_bets += 1
                    total_units += float(row['Units'])
                    
                    # Clean the percentage sign and convert to a float
                    edge_val = float(row['Edge'].replace('%', ''))
                    edges.append(edge_val)
            except Exception as e:
                # Skip rows with formatting errors
                pass
    
    if total_bets == 0:
        msg = "📊 **Weekly Accountant Report:** No bets placed this week. Bankroll preserved."
        if DISCORD_WEBHOOK_URL:
            requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": msg, "color": 8421504}]})
        return

    # Calculate the average edge across all bets
    avg_edge = sum(edges) / len(edges) if edges else 0

    # Format the Discord Executive Summary
    report = (
        f"📊 **$BEE BAKED WEEKLY REPORT** 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"**Volume:** {total_bets} +EV Bets Found\n"
        f"**Suggested Risk:** {total_units:.2f} Units\n"
        f"**Average Edge:** {avg_edge:.2f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Reminder: Log your actual closing line value (CLV) to verify market efficiency!*"
    )

    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json={
            "embeds": [{
                "description": report, 
                "color": 3066993, # Deep blue
                "image": {"url": FOOTER_IMG}
            }]
        })
        print("Accountant report sent to Discord.")

if __name__ == "__main__":
    run_accountant()