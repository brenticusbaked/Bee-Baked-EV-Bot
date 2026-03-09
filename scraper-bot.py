import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
# RotoWire's free NBA breaking news feed
RSS_URL = "https://www.rotowire.com/rss/news.php?sport=NBA"
TRACKER_FILE = "seen_news.txt"

def get_seen_news():
    """Reads the local file to see which news updates we've already alerted you about."""
    if not os.path.exists(TRACKER_FILE):
        return []
    with open(TRACKER_FILE, "r") as f:
        return [line.strip() for line in f.readlines()]

def save_seen_news(news_ids):
    """Saves the latest news IDs back to the file so we don't spam Discord."""
    # We only keep the last 50 IDs to keep the file small and fast
    with open(TRACKER_FILE, "w") as f:
        # Save the last 50 items (the most recent ones)
        for nid in news_ids[-50:]:
            f.write(f"{nid}\n")

def scrape_news():
    seen = get_seen_news()
    new_seen = seen.copy()
    alerts = []
    
    try:
        # Fetch the live news feed
        res = requests.get(RSS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if res.status_code != 200:
            print(f"Failed to fetch feed: {res.status_code}")
            return
        
        # Parse the XML data
        root = ET.fromstring(res.content)
        for item in root.findall('./channel/item'):
            guid = item.find('guid').text
            title = item.find('title').text
            desc = item.find('description').text
            link = item.find('link').text
            
            # If we haven't seen this specific news alert yet...
            if guid not in seen:
                # Append to the end of our ordered list
                new_seen.append(guid)
                
                # Check for critical injury keywords
                keywords = ["out", "injury", "questionable", "will not play", "miss", "surgery", "downgraded"]
                if any(kw in title.lower() or kw in desc.lower() for kw in keywords):
                    alerts.append({
                        "title": title, 
                        "desc": desc.replace("<p>", "").replace("</p>", ""), # Clean up basic HTML tags
                        "link": link
                    })
                    
        # Save our updated list of seen news
        save_seen_news(new_seen)
        
        # Send the alerts to Discord
        if alerts and DISCORD_WEBHOOK_URL:
            for alert in alerts:
                msg = (
                    f"🚑 **BREAKING INJURY NEWS** 🚑\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"**{alert['title']}**\n"
                    f"*{alert['desc']}*\n\n"
                    f"🔗 [Read Full Update]({alert['link']})\n"
                    f"💡 *Tip: Check backup player props immediately!*"
                )
                payload = {
                    "content": "@here", # Pings online members so you can act fast
                    "embeds": [{"description": msg, "color": 15158332}] # Emergency Red
                }
                requests.post(DISCORD_WEBHOOK_URL, json=payload)
                print(f"🚨 Alert sent: {alert['title']}")