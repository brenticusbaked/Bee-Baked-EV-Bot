import pandas as pd
import os
import requests
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def calculate_clv_report():
    if not os.path.exists('bets_log.csv'):
        return "⚠️ **No data found.** Run the scanner first to generate `bets_log.csv`!"

    try:
        # Read the log
        df = pd.read_csv('bets_log.csv')
        
        if df.empty:
            return "📭 **Log file is empty.** No bets recorded yet."

        total_bets = len(df)
        
        # Clean the 'Edge' column (remove % and convert to float)
        df['edge_val'] = df['Edge'].str.replace('%','').astype(float)
        
        # Calculate how many bets beat the sharp fair price (CLV)
        # Any edge > 0 means you got a better price than the sharp 'Fair Price'
        beat_clv_count = len(df[df['edge_val'] > 0])
        clv_pct = (beat_clv_count / total_bets) * 100
        avg_ev = df['edge_val'].mean()

        report = (
            f"📊 **WEEKLY $BEE BAKED CLV REPORT** 📊\n"
            f"📅 **Generated:** {datetime.now().strftime('%Y-%m-%d')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 **Total Alerts Logged:** {total_bets}\n"
            f"📈 **Avg. Expected Value:** {avg_ev:.2f}%\n"
            f"🏆 **Beat Closing Line (CLV):** {clv_pct:.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *A CLV over 50% confirms you are beating the house mathematically.*"
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
        print("✅ Weekly Summary sent to Discord.")
    else:
        print(report_msg)
        print("❌ Discord URL missing.")

if __name__ == "__main__":
    main()