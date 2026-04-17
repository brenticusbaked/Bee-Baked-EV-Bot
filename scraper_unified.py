import sys
from services.pipeline import run_scraper_pipeline

def run_pipeline():
    """
    Triggers the parallel execution of browser scrapers (DK, MGM, FD, etc.)
    as defined in services/pipeline.py.
    """
    print("Starting Bee-Baked Unified Scraper Pipeline...")
    try:
        run_scraper_pipeline()
    except Exception as exc:
        print(f"CRITICAL ERROR: Scraper Pipeline failed: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    run_pipeline()
