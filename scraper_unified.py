import sys
from services.pipeline import run_scraper_pipeline

def run_pipeline():
    """Restores the link to the master scraper pipeline."""
    print("Starting Bee-Baked Unified Scraper...", flush=True)
    try:
        run_scraper_pipeline()
    except Exception as exc:
        print(f"Scraper Pipeline Failed: {exc}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    run_pipeline()
