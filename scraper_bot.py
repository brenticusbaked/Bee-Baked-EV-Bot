import os
import re
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser

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
SPORT_NEWS_PAGES = {
    "NBA": "https://www.rotowire.com/basketball/news.php",
    "MLB": "https://www.rotowire.com/baseball/news.php",
    "NHL": "https://www.rotowire.com/hockey/news.php",
    "NFL": "https://www.rotowire.com/football/news.php",
    "WNBA": "https://www.rotowire.com/wnba/news.php",
    "SOCCER": "https://www.rotowire.com/soccer/news.php",
}


class _NewsHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.text = []
        self._href = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")
        if "/news/" in href or "x.com" in href:
            self._href = href

    def handle_data(self, data):
        text = " ".join(data.split())
        if text:
            self.text.append(text)
        if self._href:
            if text:
                self.links.append((self._href, text))

    def handle_endtag(self, tag):
        if tag == "a":
            self._href = None


def _feed_url(sport: str) -> str:
    return f"https://www.rotowire.com/rss/news.php?sport={sport.upper()}"


def _news_page_url(sport: str) -> str:
    return SPORT_NEWS_PAGES.get(sport, _feed_url(sport))


def _clean_text(value) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return unescape(" ".join(text.split())).strip()


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
    content = response.content.strip()
    try:
        if not content.startswith(b"<"):
            raise ET.ParseError(f"RSS feed returned non-XML content from {_feed_url(sport)}")
        root = ET.fromstring(content)
        for item in root.findall("./channel/item"):
            guid = _clean_text(item.findtext("guid") or item.findtext("link"))
            title = _clean_text(item.findtext("title"))
            desc = _clean_text(item.findtext("description"))
            link = _clean_text(item.findtext("link"))

            if not guid or has_seen_news(guid):
                continue

            if _is_relevant_update(sport, title, desc):
                alerts.append({"sport": sport, "title": title, "desc": desc, "link": link, "guid": guid})
    except ET.ParseError as exc:
        print(f"{sport} RSS parse failed, trying news page fallback: {exc}")
        alerts = _scrape_sport_news_page(sport)

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


def _scrape_sport_news_page(sport: str):
    response = request("GET", _news_page_url(sport), headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    parser = _NewsHTMLParser()
    parser.feed(response.text)

    alerts = _alerts_from_news_page_text(sport, parser.text)
    if alerts:
        return alerts

    alerts = []
    seen_titles = set()
    for href, title in parser.links:
        title = _clean_text(title)
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        link = href if href.startswith("http") else f"https://www.rotowire.com{href}"
        guid = f"{sport}:{link}:{title}"
        if has_seen_news(guid):
            continue
        if _is_relevant_update(sport, title, ""):
            alerts.append({"sport": sport, "title": title, "desc": "", "link": link, "guid": guid})
        if len(alerts) >= 10:
            break
    return alerts


def _alerts_from_news_page_text(sport: str, chunks):
    alerts = []
    month_pattern = re.compile(
        r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}$"
    )
    for index, chunk in enumerate(chunks):
        if not month_pattern.match(chunk) or index < 3:
            continue
        title = _clean_text(chunks[index - 2])
        player = _clean_text(chunks[index - 3])
        desc = _clean_text(chunks[index + 1] if index + 1 < len(chunks) else "")
        if not title:
            continue
        guid = f"{sport}:{player}:{title}:{chunk}"
        if has_seen_news(guid):
            continue
        if _is_relevant_update(sport, title, desc):
            alerts.append(
                {
                    "sport": sport,
                    "title": f"{player}: {title}" if player else title,
                    "desc": desc,
                    "link": _news_page_url(sport),
                    "guid": guid,
                }
            )
        if len(alerts) >= 10:
            break
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
