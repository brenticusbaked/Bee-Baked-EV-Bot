import sys
from services.pipeline import run_scraper_pipeline

def run_pipeline():
    """
    Executes the unified web scraper pipeline for soft books.
    This triggers DraftKings, BetMGM, FanDuel, and PrizePicks in parallel
    using the master pipeline configuration.
    """
    print("Starting Unified Scraper Pipeline...")
    try:
        run_scraper_pipeline()
    except Exception as exc:
        print(f"CRITICAL ERROR: Unified Scraper Pipeline failed to execute: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    run_pipeline()
