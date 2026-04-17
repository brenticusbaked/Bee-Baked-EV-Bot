import os
import asyncio
from playwright.async_api import async_playwright
from db_manager import save_odds_to_db

async def scrape_fd():
    url = "https://sportsbook.fanduel.com/basketball/nba"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy={
            "server": "http://p.webshare.io:80",
            "username": os.getenv("PROXY_USERNAME"),
            "password": os.getenv("PROXY_PASSWORD"),
        })
        context = await browser.new_context(user_agent=os.getenv("USER_AGENT"))
        page = await context.new_page()

        async def handle_response(response):
            if "SelectionHierarchy" in response.url or "initial-state" in response.url:
                try:
                    data = await response.json()
                    save_odds_to_db("fanduel", data)
                    print("✅ FanDuel: Market hierarchy captured.")
                except:
                    pass

        page.on("response", handle_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
        finally:
            await browser.close()
