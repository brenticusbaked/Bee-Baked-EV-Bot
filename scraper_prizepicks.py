import os
import asyncio
from playwright.async_api import async_playwright
from db_manager import save_props_to_db

async def scrape_pp():
    url = "https://app.prizepicks.com/board"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy={
            "server": "http://p.webshare.io:80",
            "username": os.getenv("PROXY_USERNAME"),
            "password": os.getenv("PROXY_PASSWORD"),
        })
        context = await browser.new_context(user_agent=os.getenv("USER_AGENT"))
        page = await context.new_page()

        async def handle_response(response):
            # PrizePicks uses a projection endpoint for their lines
            if "projections" in response.url and "league_id=7" in response.url: 
                try:
                    data = await response.json()
                    save_props_to_db("prizepicks", data)
                    print("✅ PrizePicks: Player Props captured.")
                except:
                    pass

        page.on("response", handle_response)

        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            # PrizePicks often needs a small scroll to trigger the data load
            await page.mouse.wheel(0, 1000)
            await asyncio.sleep(5) 
        finally:
            await browser.close()
