import os
import json
import asyncio
from playwright.async_api import async_playwright
from db_manager import save_odds_to_db

async def scrape_mgm():
    """
    Complete scraper for BetMGM. 
    Intercepts JSON traffic from their 'fixtures' and 'odds' endpoints.
    """
    # NBA specific endpoint for BetMGM
    url = "https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy={
                "server": "http://p.webshare.io:80",
                "username": os.getenv("PROXY_USERNAME"),
                "password": os.getenv("PROXY_PASSWORD"),
            }
        )
        
        # User Agent is critical for MGM as they have aggressive bot detection
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # DATA INTERCEPTION LOGIC
        async def handle_response(response):
            # BetMGM odds usually travel through 'mdata' or 'fixtures' endpoints
            if "fixtures" in response.url and response.status == 200:
                try:
                    data = await response.json()
                    # We check if 'fixture' data exists in the JSON blob
                    if "fixtures" in str(data).lower():
                        save_odds_to_db("betmgm", data)
                        print("✅ BetMGM: Odds data captured from fixtures endpoint.")
                except Exception as e:
                    # Silently fail if the JSON isn't odds data
                    pass

        # Attach the sniffer
        page.on("response", handle_response)

        try:
            print("BetMGM: Navigating to NBA lines via Proxy...")
            # 60s timeout to survive the initial 'Waiting Room' or slow loads
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # BetMGM sometimes loads a 'Loading' skeleton first. 
            # We wait 5 seconds to ensure the API calls actually fire.
            await asyncio.sleep(5)
            
            print("BetMGM: Session complete.")
            return True
        except Exception as e:
            print(f"❌ BetMGM Scrape Error: {e}")
            return False
        finally:
            await browser.close()

if __name__ == "__main__":
    # Test run
    asyncio.run(scrape_mgm())
