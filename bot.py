import os
import requests
import json

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
FOOTER_IMG = "https://pbs.twimg.com/media/HCM2LNraUAAKC5m?format=jpg&name=medium"

def send_debug_test(msg):
    """Simple non-embed message to test the raw webhook connection."""
    if not DISCORD_WEBHOOK_URL:
        print("❌ FAIL: DISCORD_WEBHOOK_URL is EMPTY. Check GitHub Secrets!")
        return
    
    payload = {"content": f"🛠️ **System Check:** {msg}"}
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if r.status_code == 204:
        print("✅ Success: Discord received the Test Message.")
    else:
        print(f"❌ Fail: Discord rejected Test. Code: {r.status_code}, Response: {r.text}")

def send_ev_alert(pick_text, is_emergency=False):
    """Sends the rich embed with the custom image."""
    payload = {
        "content": "@everyone" if is_emergency else "",
        "embeds": [{
            "description": pick_text,
            "color": 15158332 if is_emergency else 5763719,
            "image": {"url": FOOTER_IMG}
        }]
    }
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if r.status_code != 204:
        print(f"❌ Error sending Embed: {r.status_code} - {r.text}")

def main():
    print("--- 🚀 Starting Scan ---")
    # Step 1: Connectivity Test
    send_debug_test("Scan is starting...")

    # Step 2: Fetch your bets (using your existing get_ev_bets logic)
    picks = get_ev_bets() 
    
    if picks:
        for p in picks:
            send_ev_alert(p['msg'], p['is_emergency'])
            print("✅ Alert Sent.")
    else:
        send_ev_alert("🏀 **Scan Complete:** No +EV edges found. Bankroll safe. 🛡️")
        print("✅ 'No Edges' status sent.")

# Ensure you have your get_ev_bets function included here!
if __name__ == "__main__":
    main()