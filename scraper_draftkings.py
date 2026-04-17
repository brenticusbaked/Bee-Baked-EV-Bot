import os
import json
import asyncio
from playwright.async_api import async_playwright
from db_manager import save_odds_to_db # Ensure this helper exists in your db_manager

async def scrape_dk():
    url = "https://sportsbook.draftkings.com/leagues/basketball/nba"
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy={
                "server": "http://p.webshare.io:80",
                "username": os.getenv("PROXY_USERNAME"),
                "password": os.getenv("PROXY_PASSWORD"),
            }
        )
        context = await browser.new_context(user_agent=os.getenv("USER_AGENT"))
        page = await context.new_page()

        # INTERNAL LOGIC: Intercept the data packets
        async def handle_response(response):
            if "eventgroup" in response.url and response.status == 200:
                try:
                    data = await response.json()
                    # Logic to extract lines and save to DB
                    save_odds_to_db("draftkings", data) 
                    print("✅ DraftKings: Odds data captured and saved.")
                except Exception as e:
                    print(f"❌ DraftKings Parse Error: {e}")

        page.on("response", handle_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"❌ DraftKings Timeout/Error: {e}")
        finally:
            await browser.close()
