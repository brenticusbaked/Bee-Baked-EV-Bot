import os
import xml.etree.ElementTree as ET

from db_manager import supabase
from services.alerts import send_discord_alert
from services.http_client import request


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_INJURY_WEBHOOK_URL = os.getenv("DISCORD_INJURY_WEBHOOK_URL") or DISCORD_WEBHOOK_URL
NEWS_FEED_SPORTS = [
    sport.strip().upper()
    for sport in os.getenv("NEWS_FEED_SPORTS", "NBA,MLB,NHL,NFL,WNBA,SOCCER").split(",")
    if sport.strip()
]
SPORT_WEBHOOKS = {
    "MLB": os.getenv("DISCORD_MLB_UPDATES_WEBHOOK_URL") or DISCORD_INJURY_WEBHOOK_URL,
    "NHL": os.getenv("DISCORD_NHL_UPDATES_WEBHOOK_URL") or DISCORD_INJURY_WEBHOOK_URL,
    "NFL": os.getenv("DISCORD_NFL_UPDATES_WEBHOOK_URL") or DISCORD_INJURY_WEBHOOK_URL,
    "WNBA": os.getenv("DISCORD_WNBA_UPDATES_WEBHOOK_URL") or DISCORD_INJURY_WEBHOOK_URL,
    "SOCCER": os.getenv("DISCORD_SOCCER_UPDATES_WEBHOOK_URL") or DISCORD_INJURY_WEBHOOK_URL,
    "NBA": os.getenv("DISCORD_NBA_UPDATES_WEBHOOK_URL") or DISCORD_INJURY_WEBHOOK_URL,
}
NEWS_KEYWORDS = {
    "default": [
        "out",
        "injury",
        "injured",
        "questionable",
        "doubtful",
        "will not play",
        "miss",
        "surgery",
        "downgraded",
        "ruled out",
        "inactive",
        "lineup",
        "starting",
        "starts",
        "available",
    ],
    "MLB": [
        "probable pitcher",
        "starting pitcher",
        "starter",
        "lineup",
        "starting",
        "scratched",
        "activated",
        "injured list",
        "il",
        "out",
        "injury",
        "miss",
    ],
}


def _feed_url(sport: str) -> str:
    return f"https://www.rotowire.com/rss/news.htm?sport={sport.lower()}"


def _clean_text(value) -> str:
    return (value or "").replace("<p>", "").replace("</p>", "").strip()


def _is_relevant_update(sport: str, title: str, desc: str) -> bool:
    text = f"{title} {desc}".lower()
    keywords = NEWS_KEYWORDS.get(sport, NEWS_KEYWORDS["default"])
    return any(keyword in text for keyword in keywords)


def has_seen_news(guid):
    if not supabase:
        return False
    try:
        response = supabase.table("seen_news").select("guid").eq("guid", guid).execute()
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


def _scrape_sport_news(sport: str):
    alerts = []
    response = request("GET", _feed_url(sport), headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    root = ET.fromstring(response.content)
    for item in root.findall("./channel/item"):
        guid = _clean_text(item.findtext("guid"))
        title = _clean_text(item.findtext("title"))
        desc = _clean_text(item.findtext("description"))
        link = _clean_text(item.findtext("link"))

        if not guid or has_seen_news(guid):
            continue

        if _is_relevant_update(sport, title, desc):
            alerts.append({"sport": sport, "title": title, "desc": desc, "link": link, "guid": guid})

    for alert in alerts:
        label = "MLB STARTER / INJURY UPDATE" if sport == "MLB" else f"{sport} INJURY / NEWS UPDATE"
        message = (
            f"**{label}**\n"
            f"**{alert['title']}**\n"
            f"*{alert['desc']}*\n"
            f"[Link]({alert['link']})"
        )
        if send_discord_alert(
            {"content": "@here", "embeds": [{"description": message, "color": 15158332}]},
            source=f"scraper_bot_{sport.lower()}",
            alert_type="news_alert",
            dedupe_key=alert["guid"],
            webhook_url=SPORT_WEBHOOKS.get(sport) or DISCORD_INJURY_WEBHOOK_URL,
        ):
            mark_news_as_seen(alert["guid"])

    return alerts


def scrape_news():
    total_alerts = 0
    errors = []
    for sport in NEWS_FEED_SPORTS:
        try:
            alerts = _scrape_sport_news(sport)
            total_alerts += len(alerts)
        except Exception as exc:
            errors.append(f"{sport}: {exc}")
            print(f"{sport} news scrape error: {exc}")

    if errors:
        return {
            "detail": f"news scrape completed with errors: {' | '.join(errors)}",
            "count": total_alerts,
            "label": "alerts",
        }
    return {"detail": f"news scrape complete for {', '.join(NEWS_FEED_SPORTS)}", "count": total_alerts, "label": "alerts"}


if __name__ == "__main__":
    scrape_news()
