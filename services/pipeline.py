import asyncio
import os
from datetime import datetime

# Import the specific functions from your scrapers and fetchers
from scraper_draftkings import scrape_dk
from scraper_fanduel import scrape_fd
from scraper_betmgm import scrape_mgm
from scraper_prizepicks import scrape_pp
from master_odds_fetcher import run_fetcher
from scraper_pybaseball_fip import run_fip_scraper

async def run_master_pipeline():
    """
    Main orchestration function. Executes scrapers and API fetchers.
    """
    print(f"BEE-BAKED PIPELINE STARTING - {datetime.utcnow().isoformat()}")

    # 1. API REFRESH (Sync functions wrapped in executor if necessary, 
    # but here we assume they are fast/standard requests)
    print("--- PHASE 1: REFRESHING API DATA & CACHE ---")
    run_fetcher()
    run_fip_scraper()

    # 2. BROWSER SCRAPERS (Running concurrently to save time)
    print("--- PHASE 2: EXECUTING BROWSER SCRAPERS ---")
    scraper_tasks = [
        scrape_dk(),
        scrape_fd(),
        scrape_mgm(),
        scrape_pp()
    ]
    
    # Run all scrapers and capture results or errors
    results = await asyncio.gather(*scraper_tasks, return_exceptions=True)
    
    # Simple log of results
    names = ["DraftKings", "FanDuel", "BetMGM", "PrizePicks"]
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            print(f"❌ {names[i]} failed: {str(res)[:100]}")
        else:
            print(f"✅ {names[i]} executed successfully.")

    print(f"BEE-BAKED PIPELINE COMPLETE - {datetime.utcnow().isoformat()}")
