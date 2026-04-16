import time
import concurrent.futures
from master_odds_fetcher import run_fetcher
from unified_bot import scan_markets
from model_nba import run_nba_model
from model_nhl import run_nhl_model
from model_mlb import run_mlb_model
from bot_propodds_nba import main as run_nba_props
from clv_tracker import run_clv_tracker
from sgo_grader import run_grader
from scraper_draftkings import scrape_draftkings
from scraper_fanduel import scrape_fanduel
from scraper_prizepicks import scrape_prizepicks
from scraper_betmgm import scrape_betmgm
from scraper_bot import scrape_news

def main():
    print("🚀 BEE-BAKED SYNDICATE STARTING...")
    
    # Step 1: Pull the latest master odds cache
    run_fetcher() 
    
    # Step 2: Run Models, Scanners, and News Bots (Fast API tasks)
    print("📥 Running Models & Scanners...")
    run_nba_model()
    run_nhl_model()
    run_mlb_model()
    scan_markets()
    run_nba_props()
    scrape_news()

    # Step 3: Run Headless Scrapers in Parallel (The Bottleneck)
    print("🚀 Launching Headless Scrapers in Parallel...")
    scrapers = [scrape_draftkings, scrape_fanduel, scrape_prizepicks, scrape_betmgm]
    
    # Use ThreadPoolExecutor to run all 4 scrapers simultaneously
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        # Submit all scrapers to the executor
        futures = {executor.submit(scraper): scraper.__name__ for scraper in scrapers}
        
        # Wait for them to finish and catch any individual errors
        for future in concurrent.futures.as_completed(futures):
            scraper_name = futures[future]
            try:
                future.result() # This will raise an exception if the scraper failed
            except Exception as e:
                print(f"⚠️ {scraper_name} encountered an interruption: {e}")

    # Step 4: Post-Game Tracking & Database Updates
    print("📊 Running Post-Game Tracking...")
    run_clv_tracker()
    run_grader()
    
    print("✅ MASTER RUN COMPLETE.")

if __name__ == "__main__": 
    main()