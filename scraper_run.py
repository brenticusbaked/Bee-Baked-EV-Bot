# This file is the GitHub Actions entry point for the scraper workflow.
# It also supports local development via python-dotenv.

from dotenv import load_dotenv
load_dotenv()

from scraper_unified import run_pipeline

if __name__ == "__main__":
    run_pipeline()
