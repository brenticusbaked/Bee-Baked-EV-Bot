import pandas as pd
import os
import requests
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def calculate_clv_report():
    if not os.path.exists('bets_log.csv'):
        return "⚠️ **No data found.** The `bets_log.csv` file hasn't been created yet. Run the scanner first!"

    try:
        df = pd.read_csv('bets_log.csv')
        
        if df.empty:
            return "📭 **Log file is empty.** No bets recorded this week."

        total_bets = len(df)
        
        # CLV Logic: Did the odds we got beat the Sharp Fair Price?
        # We need to handle the string to float conversion for 'Edge'
        df['edge_val'] = df['Edge'].str.replace('%','').astype(float)
        
        # A bet beats the closing line (CLV) if Expected Value > 0
        beat_clv_count = len(df[df['edge_val'] > 0])
        clv_pct = (beat_clv_count / total_bets) * 100
        avg_ev = df['edge_val'].mean()

        report = (
            f"📊 **WEEKLY $BEE BAKED CLV REPORT** 📊\n"
            f"📅 **Generated:** {datetime.now().strftime('%Y-%m-%d')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **Total Alerts Tracked:** {total_bets}\n"
            f"📈 **Avg. Expected Value:** {avg_ev:.2f}%\n"
            f"🏆 **Beat Closing Line (CLV):** {clv_pct:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *A CLV over 50% means you are consistently outperforming the sharpest books in the world.*"
        )
        return report

    except Exception as e:
        return f"❌ **Error processing logs:** {str(e)}"

def main():
    report_msg = calculate_clv_report()
    
    payload = {
        "embeds": [{
            "title": "Weekly Market Performance",
            "description": report_msg,
            "color": 10181046, # Purple for CLV
            "image": {"url": FOOTER_IMG}
        }]
    }
    
    if DISCORD_WEBHOOK_URL:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print("✅ Weekly Summary with CLV sent to Discord.")
    else:
        print("❌ Discord URL missing.")

if __name__ == "__main__":
    main()