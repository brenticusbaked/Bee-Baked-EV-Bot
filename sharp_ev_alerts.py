import os
import requests
from datetime import datetime

# SharpAPI Configuration - strip whitespace and newlines to prevent header errors
SHARP_API_KEY = (os.environ.get("SHARPAPI_KEY") or "").strip()
DISCORD_WEBHOOK_URL = (os.environ.get("DAILY_SLIPS_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()

def fetch_sharp_ev_opportunities(sport="baseball_mlb"):
    """
    Fetches odds data with built-in +EV signals from SharpAPI via their REST endpoint.
    """
    if not SHARP_API_KEY:
        print("Error: SHARPAPI_KEY environment variable is missing or empty.")
        return None

    # Corrected URL path based on SharpAPI documentation
    url = "https://api.sharpapi.io/api/v1/odds" 
    
    headers = {
        "X-API-Key": SHARP_API_KEY,
        "Accept": "application/json"
    }
    
    params = {
        "sport": sport,
        "include_ev": "true"
    }
    
    try:
        print(f"Polling SharpAPI for {sport} +EV opportunities...")
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code == 429:
            print("SharpAPI rate limit reached (429). Skipping this cycle.")
            return None
            
        # This will trigger the exception block below if the status code is 400
        response.raise_for_status() 
        data = response.json()
        
        opportunities = []
        if isinstance(data, list):
            for event in data:
                for market in event.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        if outcome.get("ev_percent", 0) > 2.0:
                            opportunities.append({
                                "event": event.get("event_name", "Unknown Event"),
                                "selection": outcome.get("name", "Unknown Selection"),
                                "market": market.get("market_name", "Unknown Market"),
                                "sportsbook": outcome.get("sportsbook", "Unknown Book"),
                                "odds": outcome.get("price", "N/A"),
                                "ev_percent": outcome.get("ev_percent", 0.0)
                            })
        
        opportunities.sort(key=lambda x: x["ev_percent"], reverse=True)
        return opportunities
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching from SharpAPI: {e}")
        # Print the exact error message returned by SharpAPI's server
        if getattr(e, 'response', None) is not None:
            print(f"SharpAPI Server Response: {e.response.text}")
        return None
    except Exception as e:
        print(f"Unexpected error in SharpAPI fetch: {e}")
        return None

def format_discord_message(opportunities):
    """
    Formats the parsed opportunities into readable Discord embeds.
    """
    if not opportunities:
        return None
        
    embeds = []
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
    try:
        raw_opportunities = fetch_sharp_ev_opportunities(sport="baseball_mlb")
        if raw_opportunities:
            print(f"Found {len(raw_opportunities)} opportunities.")
            discord_payload = format_discord_message(raw_opportunities)
            send_to_discord(discord_payload)
        else:
            print("No +EV opportunities met the criteria at this time.")
    except Exception as e:
        print(f"SharpAPI script executed with non-fatal warning: {e}")