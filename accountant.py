import os
import csv
import requests
from datetime import datetime, timedelta

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def run_accountant():
    if not os.path.exists('bets_log.csv'): return
    
    total_risk = 0.0
    total_profit = 0.0
    wins = 0
    graded = 0
    
    with open('bets_log.csv', mode='r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['Result'] in ['WIN', 'LOSS']:
                graded += 1
                odds = int(row['Odds'].replace('+', ''))
                units = float(row['Units'])
                total_risk += units
                
                if row['Result'] == 'WIN':
                    wins += 1
                    total_profit += units * (odds/100) if odds > 0 else units * (100/abs(odds))
                else:
                    total_profit -= units

    if graded > 0:
        report = f"📊 **$BEE BAKED WEEKLY REPORT** 📊\nNet Profit: **{total_profit:+.2f} Units**\nWin Rate: {(wins/graded)*100:.1f}%"
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": report, "color": 5763719 if total_profit >= 0 else 15158332}]})

if __name__ == "__main__":
    run_accountant()