import os
import random
import string
import time
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync  # Added Stealth support

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

SITE_CONFIG = {
    "draftkings": {
        "url": "https://sportsbook.draftkings.com/leagues/basketball/nba",
        "api_marker": "api/sportscontent/v1/events",  # Broader marker
        "state_key": "tracker_draftkings_nba",
        "cache_file": "dk_lines.json",
        "color": 5763719 
    },
    "fanduel": {
        "url": "https://sportsbook.fanduel.com/basketball/nba",
        "api_marker": "api/content-managed-page",
        "state_key": "tracker_fanduel_nba",
        "cache_file": "fd_lines.json",
        "color": 15615 
    },
    "betmgm": {
        "url": "https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004",
        "api_marker": "api/v1/fixtures",
        "state_key": "tracker_betmgm_nba",
        "cache_file": "mgm_lines.json",
        "color": 13611036 
    },
    "prizepicks": {
        "url": "https://app.prizepicks.com/board",
        "api_marker": "prizepicks.com/projections", # Broader marker
        "state_key": "tracker_prizepicks_nba",
        "cache_file": "prizepicks_lines.json",
        "color": 10181046 
    }
}

def get_proxy():
    if not PROXY_IPS or not PROXY_USERNAME: return None
    session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return {
        "server": f"http://{random.choice(PROXY_IPS)}",
        "username": f"{PROXY_USERNAME}-session-{session}",
        "password": PROXY_PASSWORD,
    }

def scrape_site(playwright, site_id):
    conf = SITE_CONFIG[site_id]
    data = None
    
    # Launch browser with specific viewport and proxy
    browser = playwright.chromium.launch(headless=True, proxy=get_proxy())
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    
    page = context.new_page()
    stealth_sync(page) # Apply stealth patches to bypass navigator.webdriver detection

    def handle_response(response):
        nonlocal data
        if conf["api_marker"] in response.url:
            try:
                # Intercepting JSON responses directly from the network
                data = response.json()
            except:
                pass

    page.on("response", handle_response)
    
    try:
        # Increase timeout and use domcontentloaded for heavy sites
        page.goto(conf["url"], wait_until="domcontentloaded", timeout=60000)
        # Small random delay to mimic human behavior
        time.sleep(random.uniform(2, 5)) 
    except Exception as e:
        print(f"⚠️ {site_id} navigation timeout: {e}")

    # Final check for data with a short buffer
    if not data:
        try:
            page.wait_for_response(lambda r: conf["api_marker"] in r.url, timeout=10000)
        except:
            print(f"❌ Failed to intercept {site_id} data.")

    browser.close()
    return data

# ... [Include your process_alerts function from the previous version] ...

def run_pipeline():
    with sync_playwright() as p:
        for sid in SITE_CONFIG.keys():
            raw = scrape_site(p, sid)
            if raw: 
                process_alerts(sid, raw)
            else:
                print(f"⏭️ Skipping {sid} alert processing (no data).")
            time.sleep(random.uniform(5, 10)) # Variable delay between books

if __name__ == "__main__":
    run_pipeline()
