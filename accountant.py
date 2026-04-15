import os
import requests
from db_manager import get_all_graded_bets

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def run_accountant():
    graded_bets = get_all_graded_bets()
    if not graded_bets: return
    
    total_risk = 0.0
    total_profit = 0.0
    wins = 0
    graded = len(graded_bets)
    
    for row in graded_bets:
        odds_str = row.get('odds', '')
        if not odds_str: continue
            
        odds = int(odds_str.replace('+', ''))
        units = float(row.get('units', 0))
        total_risk += units
        
        if row.get('result') == 'WIN':
            wins += 1
            total_profit += units * (odds/100) if odds > 0 else units * (100/abs(odds))
        else:
            total_profit -= units

    if graded > 0 and DISCORD_WEBHOOK_URL:
        report = f"📊 **$BEE BAKED WEEKLY REPORT** 📊\nNet Profit: **{total_profit:+.2f} Units**\nWin Rate: {(wins/graded)*100:.1f}%"
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [{"description": report, "color": 5763719 if total_profit >= 0 else 15158332}]})

if __name__ == "__main__": run_accountant()