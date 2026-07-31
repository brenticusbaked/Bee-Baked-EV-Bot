import os
import requests
from datetime import datetime

# SharpAPI Configuration
# SharpAPI authenticates using 'X-API-Key' in the header
SHARP_API_KEY = os.environ.get("SHARPAPI_KEY") 
DISCORD_WEBHOOK_URL = os.environ.get("DAILY_SLIPS_WEBHOOK_URL") 

def fetch_sharp_ev_opportunities(sport="baseball_mlb"):
    """
    Fetches odds data with built-in +EV signals from SharpAPI via their REST endpoint.
    """
    if not SHARP_API_KEY:
        print("Error: SHARPAPI_KEY environment variable is not set.")
        return None

    # SharpAPI REST odds endpoint with EV calculations included
    url = "https://api.sharpapi.io/v1/odds" 
    
    headers = {
        "X-API-Key": SHARP_API_KEY,
        "Accept": "application/json"
    }
    
    params = {
        "sport": sport,
        "include_ev": "true" # Flag to include EV signals based on Pinnacle no-vig lines
    }
    
    try:
        print(f"Polling SharpAPI for {sport} +EV opportunities...")
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Parse the JSON array returned by the API
        # Filtering for +EV opportunities (ev_percent > 0)
        opportunities = []
        for event in data:
            for market in event.get("markets", []):
                for outcome in market.get("outcomes", []):
                    if outcome.get("ev_percent", 0) > 2.0: # Minimum 2.0% EV threshold
                        opportunities.append({
                            "event": event.get("event_name", "Unknown Event"),
                            "selection": outcome.get("name", "Unknown Selection"),
                            "market": market.get("market_name", "Unknown Market"),
                            "sportsbook": outcome.get("sportsbook", "Unknown Book"),
                            "odds": outcome.get("price", "N/A"),
                            "ev_percent": outcome.get("ev_percent", 0.0)
                        })
        
        # Sort by highest EV percentage
        opportunities.sort(key=lambda x: x["ev_percent"], reverse=True)
        return opportunities
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching from SharpAPI: {e}")
        return None

def format_discord_message(opportunities):
    """
    Formats the parsed opportunities into readable Discord embeds.
    """
    if not opportunities:
        return None
        
    embeds = []
    # Limit to top 5 to avoid spamming the Discord channel
    for opp in opportunities[:5]: 
        
        embed = {
            "title": f"🚨 Sharp Edge Detected: {opp['event']}",
            "color": 5763719,  # Green Hex
            "fields": [
                {"name": "Play", "value": f"**{opp['selection']}**\n({opp['market']})", "inline": True},
                {"name": "Sportsbook", "value": f"{opp['sportsbook']}\n{opp['odds']}", "inline": True},
                {"name": "+EV Edge", "value": f"**{opp['ev_percent']}%**", "inline": True}
            ],
            "footer": {"text": f"Powered by SharpAPI • {datetime.now().strftime('%H:%M:%S UTC')}"}
        }
        embeds.append(embed)
        
    return {"content": "New value betting opportunities found!", "embeds": embeds}

def send_to_discord(payload):
    """
    POSTs the formatted payload to your Discord webhook.
    """
    if not payload or not DISCORD_WEBHOOK_URL:
        print("No payload generated or Discord webhook URL is missing.")
        return
        
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        print(f"Successfully sent {len(payload['embeds'])} alerts to Discord.")
    except Exception as e:
        print(f"Discord webhook failed: {e}")

if __name__ == "__main__":
    # Fetch data for MLB
    raw_opportunities = fetch_sharp_ev_opportunities(sport="baseball_mlb")
    
    if raw_opportunities:
        print(f"Found {len(raw_opportunities)} opportunities.")
        discord_payload = format_discord_message(raw_opportunities)
        send_to_discord(discord_payload)
    else:
        print("No +EV opportunities met the criteria at this time.")