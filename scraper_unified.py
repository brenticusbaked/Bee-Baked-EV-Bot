import os
import random
import string
import time
from playwright.sync_api import sync_playwright

# Import your existing Bee-Baked utilities
from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

# Mapping for state tracking
SITE_CONFIG = {
    "draftkings": {
        "url": "https://sportsbook.draftkings.com/leagues/basketball/nba",
        "api_marker": "api/sportscontent/v1/events/42648",
        "state_key": "tracker_draftkings_nba",
        "cache_file": "dk_lines.json",
        "color": 5763719 # Green
    },
    "fanduel": {
        "url": "https://sportsbook.fanduel.com/basketball/nba",
        "api_marker": "api/content-managed-page",
        "state_key": "tracker_fanduel_nba",
        "cache_file": "fd_lines.json",
        "color": 15615 # Blue
    },
    "betmgm": {
        "url": "https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004",
        "api_marker": "api/v1/fixtures",
        "state_key": "tracker_betmgm_nba",
        "cache_file": "mgm_lines.json",
        "color": 13611036 # Gold
    },
    "prizepicks": {
        "url": "https://app.prizepicks.com/board",
        "api_marker": "api.prizepicks.com/projections",
        "state_key": "tracker_prizepicks_nba",
        "cache_file": "prizepicks_lines.json",
        "color": 10181046 # Purple
    }
}

def get_proxy():
    if not PROXY_IPS or not PROXY_USERNAME:
        return None
    chosen_ip = random.choice(PROXY_IPS)
    # Dynamic session ID ensures fresh residential IP per site
    session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return {
        "server": f"http://{chosen_ip}",
        "username": f"{PROXY_USERNAME}-session-{session}",
        "password": PROXY_PASSWORD,
    }

def scrape_site(playwright, site_id):
    conf = SITE_CONFIG[site_id]
    data = None
    
    print(f"🚀 Starting {site_id} scrape...")
    
    browser = playwright.chromium.launch(headless=True, proxy=get_proxy())
    context = browser.new_context(
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    # Intercept API Response
    def handle_response(response):
        nonlocal data
        if conf["api_marker"] in response.url:
            try:
                data = response.json()
            except:
                pass

    page.on("response", handle_response)

    # Bulletproof navigation with 60s timeout
    try:
        page.goto(conf["url"], wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print(f"⚠️ {site_id} page load timed out, checking for captured data anyway...")

    # Wait an extra 10s if we haven't caught the data yet
    if not data:
        try:
            page.wait_for_response(lambda r: conf["api_marker"] in r.url, timeout=10000)
        except:
            print(f"❌ Failed to intercept {site_id} API data.")

    browser.close()
    return data

def process_alerts(site_id, raw_data):
    if not raw_data: return
    
    # This is where your existing parsing logic goes for each site
    # (Keeping it brief for the unified template)
    prev_lines = load_tracker_state(SITE_CONFIG[site_id]["state_key"], SITE_CONFIG[site_id]["cache_file"])
    current_lines = {}
    alerts = []

    # [Insert your site-specific parsing logic here based on site_id]
    # Example for PrizePicks/BetMGM/FanDuel/DK logic...
    
    # Save results
    save_tracker_state(SITE_CONFIG[site_id]["state_key"], current_lines, SITE_CONFIG[site_id]["cache_file"])
    
    # Send Discord Alerts
    for msg in alerts:
        post_discord({
            "embeds": [{"description": msg, "color": SITE_CONFIG[site_id]["color"]}]
        }, webhook_url=DISCORD_WEBHOOK_URL)

def run_pipeline():
    with sync_playwright() as p:
        for site_id in SITE_CONFIG.keys():
            raw_json = scrape_site(p, site_id)
            if raw_json:
                process_alerts(site_id, raw_json)
            # Short cooldown between sites to respect proxy limits
            time.sleep(3)

if __name__ == "__main__":
    run_pipeline()
