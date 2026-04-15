import pandas as pd
import os
import requests
from datetime import datetime
from db_manager import get_all_bets

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def calculate_clv_report():
    data = get_all_bets()
    if not data:
        return "📭 **Database is empty.** No bets recorded yet."

    try:
        # Load the Supabase JSON directly into Pandas
        df = pd.DataFrame(data)
        total_bets = len(df)
        
        # SAFELY convert Edge to float by enforcing string type first, turning "SCRAPED" into NaN
        df['edge_val'] = pd.to_numeric(df['edge'].astype(str).str.replace('%', ''), errors='coerce')
        avg_ev = df['edge_val'].dropna().mean()

        # Calculate true CLV (Comparing Bet Odds to Pinnacle Closing Odds)
        df['Odds_Num'] = pd.to_numeric(df['odds'].astype(str).str.replace('+', ''), errors='coerce')
        df['CLV_Num'] = pd.to_numeric(df['closing_line_pinnacle'].astype(str).str.replace('+', ''), errors='coerce')
        
        # American odds: You beat the CLV if your Odds number is higher than the closing line
        beat_clv_count = len(df.dropna(subset=['Odds_Num', 'CLV_Num'])[df['Odds_Num'] > df['CLV_Num']])
        clv_pct = (beat_clv_count / total_bets) * 100 if total_bets > 0 else 0

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