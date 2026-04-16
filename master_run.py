import subprocess
import concurrent.futures
from master_odds_fetcher import run_fetcher
from unified_bot import scan_markets
from clv_tracker import run_clv_tracker
from sgo_grader import run_grader

def run_script(script_name):
    print(f"🚀 Launching {script_name}...")
    try:
        subprocess.run(["python", script_name], check=True)
        return f"✅ {script_name} Finished"
    except Exception as e:
        return f"❌ {script_name} Failed: {e}"

def master_pipeline():
    # STEP 1: Update the Cloud Source of Truth (Must happen first)
    print("--- PHASE 1: REFRESHING CLOUD CACHE ---")
    run_fetcher() 

    # STEP 2: Run Scrapers and Models in Parallel to save time
    print("--- PHASE 2: EXECUTING MODELS & SCRAPERS ---")
    scrapers = [
        "scraper_draftkings.py", 
        "scraper_prizepicks.py", 
        "model_nba.py", 
        "model_nhl.py"
    ]
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(executor.map(run_script, scrapers))
        for r in results: print(r)

    # STEP 3: Run the Unified Scanner (Pulls from the new cache)
    print("--- PHASE 3: UNIFIED MARKET SCAN ---")
    scan_markets()

    # STEP 4: Post-Game Processing (Grade yesterday's bets & track CLV)
    print("--- PHASE 4: POST-GAME AUDIT ---")
    run_clv_tracker()
    run_grader()

if __name__ == "__main__":
    master_pipeline()