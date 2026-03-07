import os
import requests
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
# These should be set in your GitHub Repo Secrets
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
BET_IDEAS_URL = "https://betideas.com/"

def get_betideas_predictions():
    """
    Scrapes the BetIdeas homepage for AI-generated picks.
    Returns a list of formatted strings for Discord.
    """
    picks_list = []
    try:
        # User-Agent header prevents the site from blocking the request
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(BET_IDEAS_URL, headers=headers, timeout=15)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # This targets the standard card structure on BetIdeas. 
            # We take the first 3 to avoid spamming the channel.
            prediction_cards = soup.select('.prediction-card')[:3]
            
            # DEBUG: Warn if the selectors found nothing
            if not prediction_cards:
                print("Warning: No '.prediction-card' elements found. The website's HTML might have changed.")

            for card in prediction_cards:
                try:
                    # Extracting data points
                    matchup = card.select_one('.match-name').get_text(strip=True)
                    pick = card.select_one('.bet-type').get_text(strip=True)
                    odds = card.select_one('.odds').get_text(strip=True)
                    
                    # FORMATTING FOR PLAYBOOK BOT:
                    formatted_msg = (
                        f"**🤖 AI Value Alert | BetIdeas**\n"
                        f"**Game:** {matchup}\n"
                        f"**Pick:** {pick} @ {odds}\n"
                        f"**Source:** BetIdeas AI Model\n\n"
                        f"👉 *Reply '@Playbook FanDuel' to tail instantly!*"
                    )
                    picks_list.append(formatted_msg)
                except AttributeError:
                    print(f"Warning: A prediction card was missing data (matchup, pick, or odds). Skipping.")
                    continue # Skip if a specific card is missing data
        else:
            print(f"Failed to reach BetIdeas. Status Code: {response.status_code}")
            
    except Exception as e:
        print(f"Scraping Error: {e}")
        
    return picks_list

def send_to_discord(message_content):
    """Sends a single message to your Discord server via Webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("Error: DISCORD_WEBHOOK_URL not found in environment variables. Messages will not send.")
        return

    payload = {"content": message_content}
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    
    if response.status_code == 204:
        print("Successfully posted to Discord.")
    else:
        print(f"Failed to post. Status: {response.status_code}, Response: {response.text}")

def main():
    """Main execution flow for your GitHub Action."""
    print("Starting $BEE BAKED BETS Update...")

    # 1. Fetch AI Picks from BetIdeas
    ai_picks = get_betideas_predictions()
    
    if ai_picks:
        for pick_msg in ai_picks:
            send_to_discord(pick_msg)
    else:
        print("No new AI picks found today.")

    # 2. Add your custom +EV logic or status updates here
    # Example: status_msg = "✅ Daily +EV scan complete for $BEE BAKED."
    # send_to_discord(status_msg)

if __name__ == "__main__":
    main()