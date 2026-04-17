import os
import xml.etree.ElementTree as ET

from db_manager import supabase
from services.alerts import send_discord_alert
from services.http_client import request


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
RSS_URL = "https://www.rotowire.com/rss/news.php?sport=NBA"


def has_seen_news(guid):
    if not supabase:
        return False
    try:
        response = supabase.table("seen_news").select("id").eq("guid", guid).execute()
        return len(response.data) > 0
    except Exception as exc:
        print(f"Seen news lookup failed: {exc}")
        return False


def mark_news_as_seen(guid):
    if not supabase:
        return
    try:
        supabase.table("seen_news").insert({"guid": guid}).execute()
    except Exception as exc:
        print(f"Mark news failed: {exc}")


def scrape_news():
    alerts = []
    try:
        response = request("GET", RSS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        root = ET.fromstring(response.content)
        for item in root.findall("./channel/item"):
            guid = item.find("guid").text
            title = item.find("title").text
            desc = item.find("description").text
            link = item.find("link").text

            if has_seen_news(guid):
                continue

            keywords = ["out", "injury", "questionable", "will not play", "miss", "surgery", "downgraded"]
            if any(keyword in title.lower() or keyword in desc.lower() for keyword in keywords):
                alerts.append(
                    {
                        "title": title,
                        "desc": desc.replace("<p>", "").replace("</p>", ""),
                        "link": link,
                        "guid": guid,
                    }
                )

        for alert in alerts:
            message = (
                f"**BREAKING INJURY NEWS**\n"
                f"**{alert['title']}**\n"
                f"*{alert['desc']}*\n"
                f"[Link]({alert['link']})"
            )
            if send_discord_alert(
                {"content": "@here", "embeds": [{"description": message, "color": 15158332}]},
                source="scraper_bot",
                alert_type="news_alert",
                dedupe_key=alert["guid"],
                webhook_url=DISCORD_WEBHOOK_URL,
            ):
                mark_news_as_seen(alert["guid"])
        return {"detail": "news scrape complete", "count": len(alerts), "label": "alerts"}
    except Exception as exc:
        print(f"An error occurred: {exc}")
        return {"detail": f"news scrape error: {exc}", "count": 0, "label": "alerts"}


if __name__ == "__main__":
    scrape_news()
