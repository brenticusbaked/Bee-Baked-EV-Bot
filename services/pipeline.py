import asyncio
import os
from datetime import datetime

# Ensure these match the function names in the respective files
from scraper_draftkings import scrape_dk
from scraper_fanduel import scrape_fd
from scraper_betmgm import scrape_mgm
from scraper_prizepicks import scrape_pp

async def run_master_pipeline():
    print(f"BEE-BAKED PIPELINE STARTING - {datetime.utcnow().isoformat()}")
    
    # Run scrapers concurrently
    tasks = [
        scrape_dk(),
        scrape_fd(),
        scrape_mgm(),
        scrape_pp()
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for i, res in enumerate(results):
        name = ["DraftKings", "FanDuel", "BetMGM", "PrizePicks"][i]
        if isinstance(res, Exception):
            print(f"[ERROR] {name} failed: {res}")
        else:
            print(f"[OK] {name} completed.")

def run_pipeline():
    asyncio.run(run_master_pipeline())
