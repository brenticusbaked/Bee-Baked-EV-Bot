import os
import sys
import glob

# 0. Auto-Rename all python files from hyphens to underscores to fix GitHub Actions
def fix_filenames():
    for file in glob.glob("*-*.py"):
        new_name = file.replace("-", "_")
        os.rename(file, new_name)
        print(f"🔧 Auto-renamed {file} to {new_name} for import compatibility.")

fix_filenames()

# Now it is safe to import
from model_nba import run_nba_model
from model_nhl import run_nhl_model
from bot_propodds_nba import main as run_nba_props
from unified_bot import scan_sport, flush_alerts, SPORT_CONFIGS
from clv_tracker import track_clv
from sgo_grader import run_grader

def main():
    print("🚀 Starting Bee-Baked Master Efficiency Run...")
    
    # 1. Run Situational Models (NBA/NHL)
    print("Scanning Situational Models...")
    try: run_nba_model()
    except Exception as e: print(f"NBA Model Error: {e}")
    
    try: run_nhl_model()
    except Exception as e: print(f"NHL Model Error: {e}")

    # 2. Run API Scanners (Props & Unified)
    print("Scanning Market Odds...")
    try: run_nba_props()
    except Exception as e: print(f"NBA Props Error: {e}")

    # 3. Run Unified Scan (Batching all sports into one alert)
    for sport in SPORT_CONFIGS:
        try: scan_sport(sport)
        except Exception as e: print(f"Unified {sport} Error: {e}")
    flush_alerts()

    # 4. Maintenance (CLV & Grading)
    print("Running Maintenance Task...")
    try: track_clv()
    except Exception as e: print(f"CLV Error: {e}")
    
    try: run_grader()
    except Exception as e: print(f"Grader Error: {e}")

    print("✅ Master Run Complete.")

if __name__ == "__main__":
    main()