import os

from playwright.sync_api import sync_playwright

from services.http_client import post_discord


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def scrape_draftkings():
    try:
        data = None
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(user_agent="Mozilla/5.0")
            page = context.new_page()

            def handle_response(response):
                nonlocal data
                if "api/sportscontent/v1/events/42648" in response.url:
                    try:
                        data = response.json()
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto("https://sportsbook.draftkings.com/leagues/basketball/nba", wait_until="networkidle")
            try:
                page.wait_for_response(lambda response: "api/sportscontent/v1/events/42648" in response.url, timeout=6000)
            except Exception:
                pass
            browser.close()

        if not data:
            return

        alerts = []
        for event in data.get("events", []):
            matchup = event.get("name")
            for market in event.get("markets", []):
                if market.get("name") != "Spread":
                    continue
                for outcome in market.get("outcomes", []):
                    alerts.append(
                        f"**DK Movement** | {matchup}: "
                        f"{outcome.get('label')} {outcome.get('line')} ({outcome.get('oddsAmerican')})"
                    )

        if alerts:
            post_discord({"content": "\n".join(alerts)}, webhook_url=DISCORD_WEBHOOK_URL)
    except Exception as exc:
        print(f"Error: {exc}")


if __name__ == "__main__":
    scrape_draftkings()
