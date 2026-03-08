import os
import json
from playwright.sync_api import sync_playwright

def scrape_bovada(league_url):
    """
    Scrapes Bovada by intercepting its internal JSON API responses.
    Example league_url: https://www.bovada.lv/sports/basketball/nba
    """
    with sync_playwright() as p:
        # Launching with a realistic user agent to avoid detection
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        results = []

        # Intercept the JSON response from Bovada's services API
        def handle_response(response):
            if "services/sports/event/v2/events/A/description" in response.url:
                try:
                    data = response.json()
                    results.append(data)
                except:
                    pass

        page.on("response", handle_response)
        
        print(f"Navigating to {league_url}...")
        page.goto(league_url, wait_until="networkidle")
        
        # Give it a few seconds to load extra dynamic content
        page.wait_for_timeout(5000)
        
        browser.close()
        return results

if __name__ == "__main__":
    nba_data = scrape_bovada("https://www.bovada.lv/sports/basketball/nba")
    if nba_data:
        with open("bovada_nba_raw.json", "w") as f:
            json.dump(nba_data, f, indent=4)
        print("Bovada data successfully captured.")