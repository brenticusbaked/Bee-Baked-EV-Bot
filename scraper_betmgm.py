import os
import asyncio
from playwright.async_api import async_playwright
from db_manager import save_odds_to_db

async def scrape_mgm():
    """
    BetMGM Scraper: Intercepts 'fixtures' JSON via Playwright.
    """
    url = "https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004"
    
    async with async_playwright() as p:
        # Configuration for rotating proxies
        browser = await p.chromium.launch(
            headless=True,
            proxy={
                "server": "http://p.webshare.io:80",
                "username": os.getenv("PROXY_USERNAME"),
                "password": os.getenv("PROXY_PASSWORD"),
            }
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # Listen for the specific data packets BetMGM sends
        async def handle_response(response):
            # 'fixtures' is the main endpoint for BetMGM game lines
            if "fixtures" in response.url and response.status == 200:
                try:
                    data = await response.json()
                    # Trigger the DB save function we just added
                    save_odds_to_db("betmgm", data)
                except:
                    pass

        page.on("response", handle_response)

        try:
            print("BetMGM: Navigating to NBA...")
            # Use 60s timeout for proxy lag
            await page.goto(url, wait_until="networkidle", timeout=60000)
            # Short sleep to ensure all async API calls finish
            await asyncio.sleep(5)
            return True
        except Exception as e:
            print(f"BetMGM Error: {e}")
            return False
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape_mgm())
