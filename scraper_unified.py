import sys
from services.pipeline import run_scraper_pipeline

def run_pipeline():
    """
    Triggers the parallel execution of all browser-based scrapers.
    """
    print("Starting Bee-Baked Unified Scraper...")
    try:
        run_scraper_pipeline()
    except Exception as exc:
        print(f"Scraper Pipeline Failed: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    run_pipeline()
