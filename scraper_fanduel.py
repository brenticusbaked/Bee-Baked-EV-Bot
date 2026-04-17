import os
import asyncio
from playwright.async_api import async_playwright

async def scrape_fd():
    url = "https://sportsbook.fanduel.com/basketball/nba"
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
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            print("FanDuel: Data intercepted.")
            return True
        finally:
            await browser.close()
