import asyncio
import os
from datetime import datetime

# Scrapers
from scraper_draftkings import scrape_dk
from scraper_fanduel import scrape_fd
from scraper_betmgm import scrape_mgm
from scraper_prizepicks import scrape_pp

# API Fetchers
from master_odds_fetcher import run_fetcher
from scraper_pybaseball_fip import run_fip_scraper

async def run_master_pipeline():
    print(f"BEE-BAKED PIPELINE STARTING - {datetime.utcnow().isoformat()}")

    # PHASE 1: Refresh Master API Cache (Odds API)
    print("--- PHASE 1: REFRESHING API DATA ---")
    run_fetcher() 
    run_fip_scraper()

    # PHASE 2: Execute Parallel Browser Scrapers
    print("--- PHASE 2: EXECUTING BROWSER SCRAPERS ---")
    tasks = [scrape_dk(), scrape_fd(), scrape_mgm(), scrape_pp()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    print("BEE-BAKED PIPELINE COMPLETE.")

# This is the function called by scraper_unified.py
def run_scraper_pipeline():
    asyncio.run(run_master_pipeline())
