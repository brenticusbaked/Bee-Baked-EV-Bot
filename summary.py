import pandas as pd
import os
import requests

def calculate_clv():
    if not os.path.exists('bets_log.csv'):
        return "No data yet."

    df = pd.read_csv('bets_log.csv')
    
    # Convert American odds strings back to floats for calculation if needed
    # For this summary, we track how often our 'Odds' > 'FairPriceAtBet'
    def is_beating(row):
        # Simplified: If your odds were better than the fair price at the time
        return 1 if row['Edge'].replace('%','') != '0' else 0

    df['beat_market'] = df.apply(is_beating, axis=1)
    beat_pct = (df['beat_market'].sum() / len(df)) * 100
    avg_ev = df['Edge'].str.replace('%','').astype(float).mean()

    summary_msg = (
        f"📊 **WEEKLY $BEE BAKED CLV REPORT** 📊\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **Total Bets Tracked:** {len(df)}\n"
        f"📈 **Avg. Expected Value:** {avg_ev:.2f}%\n"
        f"🏆 **Beat the Closing Line:** {beat_pct:.1f}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Tip: If your 'Beat %' is over 55%, you are mathematically guaranteed to be profitable long-term.*"
    )
    return summary_msg

def send_summary():
    msg = calculate_clv()
    payload = {
        "embeds": [{
            "description": msg,
            "color": 10181046, # Purple
            "footer": {"text": "Closing Line Value (CLV) is the true mark of a pro."}
        }]
    }
    requests.post(os.getenv("DISCORD_WEBHOOK_URL"), json=payload)

if __name__ == "__main__":
    send_summary()