import os
import csv
import requests

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def american_to_decimal(american_str):
    """Safely converts American odds strings to Decimal for math comparison."""
    try:
        american = float(american_str.replace('+', '').strip())
        if american > 0:
            return (american / 100.0) + 1.0
        elif american < 0:
            return (100.0 / abs(american)) + 1.0
        return 1.0
    except ValueError:
        return 0.0

def run_clv_analysis():
    if not os.path.exists('bets_log.csv'):
        print("No bets log found.")
        return

    total_bets_with_clv = 0
    clv_beaten = 0
    total_clv_value = 0.0

    with open('bets_log.csv', 'r') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return
            
        clv_idx = 8
        odds_idx = 4
        
        for row in reader:
            if not row or not any(row) or len(row) <= clv_idx:
                continue
                
            taken_odds_str = row[odds_idx]
            closing_odds_str = row[clv_idx]
            
            # Only analyze if Pinnacle gave us a closing line
            if closing_odds_str.strip() and closing_odds_str.strip() != "N/A":
                taken_dec = american_to_decimal(taken_odds_str)
                closing_dec = american_to_decimal(closing_odds_str)
                
                if taken_dec > 0 and closing_dec > 0:
                    total_bets_with_clv += 1
                    
                    # Decimal > Decimal means better payout (e.g., 2.10 > 1.90)
                    if taken_dec > closing_dec:
                        clv_beaten += 1
                        
                    # Calculate exact percentage edge over the closing line
                    edge_over_close = (taken_dec / closing_dec) - 1
                    total_clv_value += edge_over_close

    if total_bets_with_clv > 0:
        win_rate = (clv_beaten / total_bets_with_clv) * 100
        avg_clv_edge = (total_clv_value / total_bets_with_clv) * 100
        
        print(f"Analyzed {total_bets_with_clv} bets. CLV Beaten: {win_rate:.1f}%")
        
        if DISCORD_WEBHOOK_URL:
            # Green if beating the market > 50%, Red if losing to the market
            color = 5763719 if win_rate >= 50 else 15158332
            
            msg = (
                f"📈 **$BEE BAKED SHARP METRICS** 📈\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"**Total Bets Tracked:** {total_bets_with_clv}\n"
                f"**CLV Beaten Rate:** {win_rate:.1f}%\n"
                f"**Avg Edge vs Close:** {avg_clv_edge:+.2f}%\n\n"
                f"*Note: Consistently beating the Pinnacle close > 50% guarantees a long-term mathematical advantage.*"
            )
            requests.post(DISCORD_WEBHOOK_URL, json={
                "embeds": [{"description": msg, "color": color, "image": {"url": FOOTER_IMG}}]
            })
    else:
        print("Not enough CLV data to analyze yet.")

if __name__ == "__main__":
    run_clv_analysis()