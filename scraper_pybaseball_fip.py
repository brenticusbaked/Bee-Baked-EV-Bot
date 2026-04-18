import os
import pybaseball
from db_manager import save_odds_to_db

<<<<<<< Updated upstream
def run_fip_scraper():
    """
    Fetches pitcher FIP and ERA data from FanGraphs using pybaseball.
    Saves the data to Supabase for the MLB model to use.
    """
    print("Starting FanGraphs/pybaseball FIP Scraper...")
    
    try:
        # Fetch pitching stats for the current 2026 season. 
        stats = pybaseball.pitching_stats(2026, qual=0)
        
        # Target specific columns for the EV model
        target_columns = ['Name', 'Team', 'FIP', 'ERA', 'IP']
        
        if set(target_columns).issubset(stats.columns):
            # Convert to a JSON-friendly format
            clean_data = stats[target_columns].dropna().to_dict(orient='records')
            
            # Save it directly to the database
            save_odds_to_db("fangraphs_fip", {"pitchers": clean_data})
            print("✅ FanGraphs FIP data successfully fetched and cached.")
        else:
            print("❌ FanGraphs Scraper: Expected columns (like FIP) were missing.")

    except Exception as e:
        print(f"❌ pybaseball scrape error: {e}")

if __name__ == "__main__":
    run_fip_scraper()
=======
STATE_KEY = "mlb_fip_cache"
CACHE_FILE = "fip_cache.json"

PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

def scrape_fip():
    print("Initializing pybaseball FIP scraper...")
    season = datetime.now().year
    
    if PROXY_IPS and PROXY_USERNAME and PROXY_PASSWORD:
        chosen_ip = random.choice(PROXY_IPS)
        proxy_url = f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{chosen_ip}"
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        print("Routing FanGraphs request through residential proxy...")

    try:
        stats = pybaseball.pitching_stats(season, qual=0)
        chadwick = pybaseball.chadwick_register()
        merged = stats.merge(chadwick, left_on='IDfg', right_on='key_fangraphs', how='inner')
        
        fip_cache = {}
        for _, row in merged.iterrows():
            mlbam_id = row.get('key_mlbam')
            fip = row.get('FIP')
            era = row.get('ERA')
            
            if pd.notna(mlbam_id) and pd.notna(fip):
                fip_cache[str(int(mlbam_id))] = {
                    "fip": float(fip),
                    "era": float(era) if pd.notna(era) else 9.99
                }
                
        save_tracker_state(STATE_KEY, fip_cache, CACHE_FILE)
        print(f"Successfully scraped and cached Actual FIP for {len(fip_cache)} MLB pitchers.")
        return {"detail": "pybaseball fip scrape complete", "count": len(fip_cache), "label": "updates"}
        
    except Exception as exc:
        print(f"Error scraping FIP via pybaseball: {exc}")
        return {"detail": f"pybaseball scrape error: {exc}", "count": 0, "label": "updates"}
    finally:
        if "HTTP_PROXY" in os.environ: del os.environ["HTTP_PROXY"]
        if "HTTPS_PROXY" in os.environ: del os.environ["HTTPS_PROXY"]

if __name__ == "__main__":
    scrape_fip()
>>>>>>> Stashed changes
