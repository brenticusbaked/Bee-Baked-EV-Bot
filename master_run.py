import time
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

def main():
    print("🚀 BEE-BAKED SYNDICATE STARTING...")
    run_fetcher() 
    
    # Models & Scanners
    run_nba_model()
    run_nhl_model()
    run_mlb_model()
    scan_markets()
    run_nba_props()

    # Headless Scrapers (DraftKings, FanDuel, PrizePicks, MGM)
    try: scrape_draftkings(); scrape_fanduel(); scrape_prizepicks(); scrape_betmgm()
    except: print("Headless Scraper Interruption")

    # Post-Game Tracking
    run_clv_tracker()
    run_grader()
    print("✅ MASTER RUN COMPLETE.")

if __name__ == "__main__": main()