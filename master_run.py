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