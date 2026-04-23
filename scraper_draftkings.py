import asyncio
import os
from typing import Dict, Iterable, List, Optional

from playwright.async_api import async_playwright

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "dk_lines.json"
STATE_KEY = "tracker_draftkings_nba"
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)


def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)


def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)


def _iter_offer_groups(payload: dict) -> Iterable[dict]:
    event_group = payload.get("eventGroup") or payload.get("eventgroup") or {}
    for category in event_group.get("offerCategories", []):
        for descriptor in category.get("offerSubcategoryDescriptors", []):
            subcategory = descriptor.get("offerSubcategory", {})
            for offer_group in subcategory.get("offers", []):
                if isinstance(offer_group, list):
                    for offer in offer_group:
                        if isinstance(offer, dict):
                            yield offer
                elif isinstance(offer_group, dict):
                    yield offer_group


def _build_event_name_map(payload: dict) -> Dict[str, str]:
    event_group = payload.get("eventGroup") or payload.get("eventgroup") or {}
    event_name_map = {}
    for event in event_group.get("events", []):
        event_id = str(event.get("eventId") or event.get("id") or "")
        name = event.get("name") or event.get("shortName") or event.get("eventName") or "Unknown Matchup"
        if event_id:
            event_name_map[event_id] = name
    return event_name_map


def _parse_spread_lines(payload: dict) -> Dict[str, Dict[str, object]]:
    event_name_map = _build_event_name_map(payload)
    current_lines: Dict[str, Dict[str, object]] = {}

    for offer in _iter_offer_groups(payload):
        label = str(offer.get("label") or offer.get("criterion", {}).get("label") or "").lower()
        if "spread" not in label:
            continue

        event_id = str(offer.get("eventId") or offer.get("eventIdLong") or "")
        matchup = event_name_map.get(event_id, "Unknown Matchup")

        for outcome in offer.get("outcomes", []):
            team = outcome.get("label") or outcome.get("participant") or outcome.get("name")
            line = outcome.get("line") or outcome.get("spread") or outcome.get("lineValue")
            if team in (None, "") or line in (None, "") or not event_id:
                continue

            unique_key = f"{event_id}_{team}"
            current_lines[unique_key] = {"matchup": matchup, "team": team, "line": line}

    return current_lines


async def scrape_dk():
    url = "https://sportsbook.draftkings.com/leagues/basketball/nba"
    api_data = None

    async with async_playwright() as playwright:
        proxy_settings = None
        if PROXY_USERNAME and PROXY_PASSWORD:
            proxy_settings = {
                "server": "http://p.webshare.io:80",
                "username": PROXY_USERNAME,
                "password": PROXY_PASSWORD,
            }

        browser = await playwright.chromium.launch(headless=True, proxy=proxy_settings)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            print("DraftKings: Navigating to NBA lines...")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception:
                print("DraftKings navigation timeout caught gracefully. Checking for API data anyway...")

            try:
                response = await page.wait_for_response(
                    lambda r: "eventgroups" in r.url.lower() and ("api" in r.url.lower() or "sites" in r.url.lower()),
                    timeout=8000,
                )
                api_data = await response.json()
            except Exception as exc:
                print(f"DraftKings API wait failed: {exc}")
        finally:
            await browser.close()

    if not api_data:
        print("Could not intercept DraftKings API data via Playwright.")
        return {"detail": "draftkings scrape complete", "count": 0, "label": "alerts"}

    try:
        current_lines = _parse_spread_lines(api_data)
        previous_lines = load_previous_lines()
        alerts: List[str] = []

        for unique_key, current in current_lines.items():
            previous = previous_lines.get(unique_key)
            if not previous:
                continue

            try:
                old_line = float(previous["line"])
                new_line = float(current["line"])
            except (TypeError, ValueError):
                continue

            if abs(new_line - old_line) >= 1.5:
                alerts.append(
                    f"**DK STEAM ALERT:** {current['matchup']}\n"
                    f"**{current['team']} Spread Moved!**\n"
                    f"Old Line: {old_line} -> **New Line: {new_line}**"
                )

        save_current_lines(current_lines)
        for message in alerts:
            post_discord({"embeds": [{"description": message, "color": 15844367}]}, webhook_url=DISCORD_WEBHOOK_URL)
        return {"detail": "draftkings scrape complete", "count": len(alerts), "label": "alerts"}
    except Exception as exc:
        print(f"DraftKings Scrape Error: {exc}")
        return {"detail": f"draftkings scrape error: {exc}", "count": 0, "label": "alerts"}


def scrape_draftkings():
    return asyncio.run(scrape_dk())


if __name__ == "__main__":
    asyncio.run(scrape_dk())
