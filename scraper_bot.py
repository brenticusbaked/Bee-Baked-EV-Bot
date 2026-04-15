import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from db_manager import supabase # Reusing your existing connection

# --- CONFIG ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
RSS_URL = "https://www.rotowire.com/rss/news.php?sport=NBA"

def has_seen_news(guid):
    """Checks Supabase to see if this news ID has already been alerted."""
    if not supabase: return False
    res = supabase.table("seen_news").select("id").eq("guid", guid).execute()
    return len(res.data) > 0

def mark_news_as_seen(guid):
    """Saves the news GUID to Supabase."""
    if not supabase: return
    supabase.table("seen_news").insert({"guid": guid}).execute()

def scrape_news():
    alerts = []
    try:
        res = requests.get(RSS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if res.status_code != 200: return
        
        root = ET.fromstring(res.content)
        for item in root.findall('./channel/item'):
            guid = item.find('guid').text
            title = item.find('title').text
            desc = item.find('description').text
            link = item.find('link').text
            
            # Use Database check instead of local text file
            if not has_seen_news(guid):
                keywords = ["out", "injury", "questionable", "will not play", "miss", "surgery", "downgraded"]
                if any(kw in title.lower() or kw in desc.lower() for kw in keywords):
                    alerts.append({"title": title, "desc": desc.replace("<p>", "").replace("</p>", ""), "link": link, "guid": guid})

        if alerts and DISCORD_WEBHOOK_URL:
            for alert in alerts:
                # 1. Send Alert
                msg = f"🚑 **BREAKING INJURY NEWS** 🚑\n**{alert['title']}**\n*{alert['desc']}*\n🔗 [Link]({alert['link']})"
                requests.post(DISCORD_WEBHOOK_URL, json={"content": "@here", "embeds": [{"description": msg, "color": 15158332}]})
                
                # 2. Mark as seen in DB immediately
                mark_news_as_seen(alert['guid'])
                print(f"🚨 Alert sent: {alert['title']}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    scrape_news()