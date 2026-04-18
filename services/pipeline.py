import asyncio
import os
from datetime import datetime

from scraper_draftkings import scrape_dk
from scraper_fanduel import scrape_fd
from scraper_betmgm import scrape_mgm  
from scraper_prizepicks import scrape_pp
from scraper_pybaseball_fip import run_fip_scraper

async def run_master_pipeline():
    print(f"BEE-BAKED PIPELINE STARTING - {datetime.utcnow().isoformat()}")
    
    loop = asyncio.get_running_loop()
    
    print("--- PHASE 1: REFRESHING API DATA ---")
    try:
        # FIXED: We must 'await' the async FanGraphs scraper
        await run_fip_scraper()
    except Exception as e:
        print(f"[ERROR] FanGraphs FIP failed: {e}")

    print("--- PHASE 2: EXECUTING BROWSER SCRAPERS ---")
    
    tasks = [
        asyncio.create_task(scrape_dk()),  
        loop.run_in_executor(None, scrape_fd),
        loop.run_in_executor(None, scrape_mgm),
        loop.run_in_executor(None, scrape_pp)
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
