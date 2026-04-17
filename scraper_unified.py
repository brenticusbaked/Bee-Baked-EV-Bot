import os
import random
import string
import time
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth  # Correct v2.x import

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
        "api_marker": "api/sportscontent/v1/events",
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
        "api_marker": "prizepicks.com/projections",
        "state_key": "tracker_prizepicks_nba",
        "cache_file": "prizepicks_lines.json",
        "color": 10181046 
    }
}

def get_proxy_settings():
    if not PROXY_IPS or not PROXY_USERNAME: return None
    session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return {
        "server": f"http://{random.choice(PROXY_IPS)}",
        "username": f"{PROXY_USERNAME}-session-{session}",
        "password": PROXY_PASSWORD,
    }

def scrape_site(playwright_instance, site_id):
    conf = SITE_CONFIG[site_id]
    data = None
    
    # Launch browser through the proxy
    browser = playwright_instance.chromium.launch(headless=True, proxy=get_proxy_settings())
    
    # Create context with a realistic viewport and User-Agent
    context = browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    
    page = context.new_page()

    def handle_response(response):
        nonlocal data
        if conf["api_marker"] in response.url:
            try:
                data = response.json()
            except:
                pass

    page.on("response", handle_response)
    
    try:
        # Use domcontentloaded to capture API data as early as possible
        page.goto(conf["url"], wait_until="domcontentloaded", timeout=60000)
        time.sleep(random.uniform(5, 8)) # Give extra time for heavy background APIs to fire
    except Exception as e:
        print(f"⚠️ {site_id} timed out visually, checking for intercepted data...")

    # If the page timed out, it might still have captured the background API!
    if not data:
        try:
            page.wait_for_response(lambda r: conf["api_marker"] in r.url, timeout=15000)
        except:
            print(f"❌ No data captured for {site_id}")

    browser.close()
    return data

def run_pipeline():
    # Recommended v2.x usage: Wrap the entire execution in the Stealth context
    with Stealth().use_sync(sync_playwright()) as playwright:
        for sid in SITE_CONFIG.keys():
            raw = scrape_site(playwright, sid)
            if raw: 
                # [Invoke your process_alerts(sid, raw) logic here]
                print(f"✅ Data successfully captured for {sid}")
            time.sleep(random.uniform(5, 12))

if __name__ == "__main__":
    run_pipeline()
