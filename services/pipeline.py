import asyncio
import os
from datetime import datetime

# 1. FIXED IMPORTS: Matching the exact names defined in your scraper files
from scraper_draftkings import scrape_dk
from scraper_fanduel import scrape_fanduel
from scraper_betmgm import scrape_betmgm
from scraper_prizepicks import scrape_prizepicks
from scraper_pybaseball_fip import run_fip_scraper

async def run_master_pipeline():
    print(f"BEE-BAKED PIPELINE STARTING - {datetime.utcnow().isoformat()}")
    
    loop = asyncio.get_running_loop()
    
    print("--- PHASE 1: REFRESHING API DATA ---")
    try:
        # FIXED: run_fip_scraper uses sync_playwright, so it must run in an executor
        # to prevent it from crashing the asyncio loop.
        await loop.run_in_executor(None, run_fip_scraper)
    except Exception as e:
        print(f"[ERROR] FanGraphs FIP failed: {e}")

    print("--- PHASE 2: EXECUTING BROWSER SCRAPERS ---")
    
    # 2. FIXED EXECUTORS: DraftKings is async, but the rest are sync Playwright bots.
    tasks = [
        asyncio.create_task(scrape_dk()),  
        loop.run_in_executor(None, scrape_fanduel),
        loop.run_in_executor(None, scrape_betmgm),
        loop.run_in_executor(None, scrape_prizepicks)
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    names = ["DraftKings", "FanDuel", "BetMGM", "PrizePicks"]
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            print(f"[ERROR] {names[i]} failed: {res}")
        else:
            print(f"[OK] {names[i]} completed.")

def run_scraper_pipeline():
    asyncio.run(run_master_pipeline())

def run_pipeline():
    asyncio.run(run_master_pipeline())
