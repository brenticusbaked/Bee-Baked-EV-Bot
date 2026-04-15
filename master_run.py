import os
import time

# Core Data Acquisition
from master_odds_fetcher import run_fetcher

# Scanner & Models
from unified_bot import scan_markets  # Fixed: Matches the function name in unified_bot.py
from model_nba import run_nba_model
from model_nhl import run_nhl_model
from model_mlb import run_mlb_model
from bot_propodds_nba import main as run_nba_props

# Post-Game Audits & Performance
from clv_tracker import run_clv_tracker # Fixed: Matches the function name in clv_tracker.py
from sgo_grader import run_grader

# Standalone Scrapers (Headless Playwright)
from scraper_bot import (
    scrape_draftkings, 
    scrape_fanduel, 
    scrape_prizepicks, 
    scrape_betmgm, 
    scrape_news
)

def main():
    print("🚀 --- BEE-BAKED SYNDICATE: MASTER RUN STARTING --- 🚀")
    start_time = time.time()

    # 1. Fetch Master Odds Cache (Uses 6 Credits Total)
    print("Fetching Global Odds Data...")
    try: 
        run_fetcher() 
    except Exception as e: 
        print(f"Fetcher Error: {e}")
        return # Stop execution if data cannot be fetched

    # 2. Run Situational Predictive Models
    print("Running Situational Models...")
    try: run_nba_model()
    except Exception as e: print(f"NBA Model Error: {e}")
    
    try: run_nhl_model()
    except Exception as e: print(f"NHL Model Error: {e}")
    
    try: run_mlb_model()
    except Exception as e: print(f"MLB Model Error: {e}")

    # 3. Run Unified Market Scanner (KY + Offshore + DFS)
    print("Running Unified Market Scan...")
    try: 
        scan_markets() 
    except Exception as e: 
        print(f"Unified Scan Error: {e}")

    # 4. Run Player Prop Engine
    print("Running Player Prop Analysis...")
    try: run_nba_props()
    except Exception as e: print(f"Prop Bot Error: {e}")

    # 5. Run Live Scrapers (Steam & Breaking News)
    print("Running Headless Scrapers...")
    try: scrape_draftkings()
    except Exception as e: print(f"DK Scraper Error: {e}")
    
    try: scrape_fanduel()
    except Exception as e: print(f"FD Scraper Error: {e}")

    try: scrape_prizepicks()
    except Exception as e: print(f"PrizePicks Scraper Error: {e}")

    try: scrape_betmgm()
    except Exception as e: print(f"MGM Scraper Error: {e}")

    try: scrape_news()
    except Exception as e: print(f"News Scraper Error: {e}")

    # 6. Post-Game Auditing (CLV & Result Grading)
    print("Running Audits & Grading...")
    try: 
        run_clv_tracker() 
    except Exception as e: 
        print(f"CLV Audit Error: {e}")
        
    try: 
        run_grader() 
    except Exception as e: 
        print(f"Grader Error: {e}")

    elapsed = time.time() - start_time
    print(f"✅ --- MASTER RUN COMPLETE (Total Time: {elapsed:.2f}s) --- ✅")

if __name__ == "__main__":
    main()