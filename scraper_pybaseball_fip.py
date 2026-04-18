import os
import random
import pandas as pd
import pybaseball
import asyncio
from playwright.async_api import async_playwright
from datetime import datetime
from db_manager import save_tracker_state

STATE_KEY = "mlb_fip_cache"
CACHE_FILE = "fip_cache.json"

PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

async def run_fip_scraper():
    print("Initializing Async Playwright FanGraphs Scraper (Bypassing pybaseball 403)...")
    season = datetime.now().year
    
    fg_data = None
    
    async with async_playwright() as p:
        proxy_settings = None
        if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
            chosen_ip = random.choice(PROXY_IPS)
            proxy_settings = {
                "server": f"http://{chosen_ip}",
                "username": PROXY_USERNAME,
                "password": PROXY_PASSWORD,
            }

        browser = await p.chromium.launch(headless=True, proxy=proxy_settings)
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # In async playwright, we must await the .json() parsing
        async def handle_response(response):
            nonlocal fg_data
            if "api/leaders/major-league/data" in response.url:
                try:
                    json_response = await response.json()
                    if "data" in json_response:
                        fg_data = json_response["data"]
                except Exception:
                    pass

        page.on("response", handle_response)
        
        try:
            url = f"https://www.fangraphs.com/leaders/major-league?pos=all&stats=pit&lg=all&qual=0&type=8&season={season}&month=0"
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            print(f"FanGraphs DOM timeout caught gracefully. Checking for intercepted API data...")
            
        try:
            if not fg_data:
                await page.wait_for_response(lambda res: "api/leaders/major-league/data" in res.url, timeout=5000)
        except Exception:
            pass
            
        await browser.close()

    if not fg_data:
        print("Error: Could not intercept FanGraphs API data via Playwright.")
        return {"detail": "fangraphs scrape error: no data", "count": 0, "label": "updates"}

    try:
        stats = pd.DataFrame(fg_data)
        chadwick = pybaseball.chadwick_register()
        
        stats['PlayerId'] = stats['PlayerId'].astype(str)
        chadwick['key_fangraphs'] = chadwick['key_fangraphs'].astype(str)
        
        merged = stats.merge(chadwick, left_on='PlayerId', right_on='key_fangraphs', how='inner')
        
        fip_cache = {}
        for _, row in merged.iterrows():
            mlbam_id = row.get('key_mlbam')
            fip = row.get('FIP')
            era = row.get('ERA')
            
            if pd.notna(mlbam_id) and pd.notna(fip):
                fip_cache[str(int(mlbam_id))] = {
                    "fip": float(fip),
                    "era": float(era) if pd.notna(era) else 9.99
                }
                
        save_tracker_state(STATE_KEY, fip_cache, CACHE_FILE)
        print(f"Successfully scraped and cached Actual FIP for {len(fip_cache)} MLB pitchers.")
        
        return {"detail": "playwright fip scrape complete", "count": len(fip_cache), "label": "updates"}
        
    except Exception as exc:
        print(f"Error processing FanGraphs data: {exc}")
        return {"detail": f"fangraphs processing error: {exc}", "count": 0, "label": "updates"}

if __name__ == "__main__":
    asyncio.run(run_fip_scraper())
