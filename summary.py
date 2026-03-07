import pandas as pd
import os
import requests
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

def calculate_clv():
    if not os.path.exists('bets_log.csv'):
        return "⚠️ No log file found. Run the scanner first!"

    df = pd.read_csv('bets_log.csv')
    if df.empty:
        return "📭 Log file is empty."

    # CLV Math: Did we beat the market?
    # We compare our Odds to the Fair Sharp Price
    total_bets = len(df)
    # Beating the market means your Odds > FairPriceAtBet
    # (Assuming columns: Odds, Edge, Sharp Price)
    df['edge_val'] = df['Edge'].str.replace('%','').astype(float)
    beat_clv = len(df[df['edge_val'] > 0])
    clv_pct = (beat_clv / total_bets) * 100

    return (
        f"📊 **WEEKLY $BEE BAKED CLV REPORT** 📊\n"
        f"🎯 **Total Bets:** {total_bets}\n"
        f"🏆 **Beat Closing Line:** {clv_pct:.1f}%\n"
        f"📈 **Avg Edge:** {df['edge_val'].mean():.2f}%\n"
    )

if __name__ == "__main__":
    report = calculate_clv()
    # Send report to Discord using requests.post...
    print(report)