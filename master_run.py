import os
from master_odds_fetcher import run_fetcher
from model_nba import run_nba_model
from model_nhl import run_nhl_model
from model_mlb import run_mlb_model
from bot_propodds_nba import main as run_nba_props
from unified_bot import scan_sport, flush_alerts, SPORT_CONFIGS
from clv_tracker import track_clv
from sgo_grader import run_grader
from clv_analyzer import run_clv_analysis

def main():
    print("🚀 Starting Bee-Baked Master Efficiency Run...")
    
    # 0. Populate API Cache
    run_fetcher()
    
    # 1. Run Situational Models
    print("Scanning Situational Models...")
    try: run_nba_model()
    except Exception as e: print(f"NBA Model Error: {e}")
    
    try: run_nhl_model()
    except Exception as e: print(f"NHL Model Error: {e}")

    try: run_mlb_model()
    except Exception as e: print(f"MLB Model Error: {e}")

    # 2. Run API Scanners
    print("Scanning Market Odds...")
    try: run_nba_props()
    except Exception as e: print(f"NBA Props Error: {e}")

    # 3. Run Unified Scan
    for sport in SPORT_CONFIGS:
        try: scan_sport(sport)
        except Exception as e: print(f"Unified {sport} Error: {e}")
    flush_alerts()

    # 4. Maintenance
    print("Running Maintenance Task...")
    try: track_clv()
    except Exception as e: print(f"CLV Error: {e}")
    
    try: run_grader()
    except Exception as e: print(f"Grader Error: {e}")

    # 5. Reporting
    print("Generating Sharp Metrics Report...")
    try: run_clv_analysis()
    except Exception as e: print(f"CLV Analyzer Error: {e}")

    print("✅ Master Run Complete.")

if __name__ == "__main__":
    main()