import os
import requests

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# --- ODDS API PARAMETERS ---
# By keeping parameters strict, we ensure this costs exactly ONE credit per run.
SPORT = 'upcoming' # Gets the next 8 upcoming games across major sports
REGIONS = 'us'
MARKETS = 'h2h' # Moneyline only
BOOKMAKERS = 'fanduel' # Filters only FanDuel to save bandwidth and match your Playbook bot
ODDS_FORMAT = 'american'

def get_odds_data():
    """
    Fetches live moneyline odds from The Odds API for FanDuel.
    Costs exactly 1 request per execution.
    """
    if not ODDS_API_KEY:
        print("Error: ODDS_API_KEY is missing. Add it to GitHub Secrets.")
        return []

    picks_list = []
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
    
    params = {
        'apiKey': ODDS_API_KEY,
        'regions': REGIONS,
        'markets': MARKETS,
        'bookmakers': BOOKMAKERS,
        'oddsFormat': ODDS_FORMAT
    }

    try:
        print("Fetching data from The Odds API...")
        response = requests.get(url, params=params, timeout=15)
        
        # Check remaining credits via response headers (Great for debugging!)
        requests_remaining = response.headers.get('x-requests-remaining')
        print(f"Odds API Credits Remaining: {requests_remaining}")

        if response.status_code == 200:
            games_data = response.json()
            
            # We will just grab the first 3 games to avoid Discord spam
            for game in games_data[:3]:
                try:
                    matchup = f"{game['away_team']} @ {game['home_team']}"
                    
                    # Digging into the JSON structure to find the FanDuel odds
                    bookmaker = game['bookmakers'][0] # We filtered for FanDuel, so it's the first one
                    market = bookmaker['markets'][0]  # We filtered for h2h (moneyline)
                    outcomes = market['outcomes']
                    
                    # Find the team with the lowest odds (The Favorite)
                    favorite = min(outcomes, key=lambda x: x['price'])
                    pick_team = favorite['name']
                    pick_odds = favorite['price']
                    
                    # Add a plus sign to positive odds for clean formatting
                    if pick_odds > 0:
                        pick_odds = f"+{pick_odds}"
                        
                    formatted_msg = (
                        f"**📈 Live Market Odds | The Odds API**\n"
                        f"**Game:** {matchup}\n"
                        f"**Play:** {pick_team} ML @ {pick_odds}\n"
                        f"**Book:** FanDuel\n\n"
                        f"👉 *Reply '@Playbook FanDuel' to tail instantly!*"
                    )
                    picks_list.append(formatted_msg)
                    
                except (IndexError, KeyError):
                    print(f"Skipping {game.get('home_team')} - Odds not fully posted yet.")
                    continue
        elif response.status_code == 401:
            print("API Key is invalid or unauthorized.")
        elif response.status_code == 429:
            print("You have hit your Odds API rate limit/credit limit!")
        else:
            print(f"Failed to fetch. Status: {response.status_code}, Body: {response.text}")
            
    except Exception as e:
        print(f"API Request Error: {e}")
        
    return picks_list

def send_to_discord(message_content):
    """Sends a single message to your Discord server via Webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("Error: DISCORD_WEBHOOK_URL missing. Cannot send to Discord.")
        return

    payload = {"content": message_content}
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    
    if response.status_code == 204:
        print("Successfully posted to Discord.")
    else:
        print(f"Failed to post to Discord. Status: {response.status_code}")

def main():
    print("Starting $BEE BAKED BETS Live Odds Pull...")

    live_picks = get_odds_data()
    
    if live_picks:
        for pick in live_picks:
            send_to_discord(pick)
    else:
        print("No playable odds found for FanDuel at this time.")

if __name__ == "__main__":
    main()