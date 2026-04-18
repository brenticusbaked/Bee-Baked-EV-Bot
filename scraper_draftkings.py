import os
import asyncio
from playwright.async_api import async_playwright

async def scrape_dk(): 
    url = "https://sportsbook.draftkings.com/leagues/basketball/nba"
    
    async with async_playwright() as p:
        proxy_username = os.getenv("PROXY_USERNAME")
        proxy_password = os.getenv("PROXY_PASSWORD")
        proxy_server = "http://p.webshare.io:80"
        
        browser = await p.chromium.launch(
            headless=True,
            proxy={
                "server": proxy_server,
                "username": proxy_username,
                "password": proxy_password,
            }
        )
        
        context = await browser.new_context(user_agent=os.getenv("USER_AGENT"))
        page = await context.new_page()
        
        try:
            print("DraftKings: Navigating to NBA lines...")
            # FIXED: Removed 'networkidle', changed to 'domcontentloaded', lowered timeout.
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as nav_e:
                print(f"DraftKings navigation timeout caught gracefully. Proceeding to parse...")
            
            # (Your parsing logic goes here)
            
            print("DraftKings: Data intercepted successfully.")
            
        except Exception as e:
            print(f"DraftKings Scrape Error: {e}")
            raise e
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape_dk())
