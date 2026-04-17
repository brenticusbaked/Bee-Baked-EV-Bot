import os
import random
import string
import time
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL")
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
    
    # Randomize viewport to mimic different users
    viewports = [{'width': 1920, 'height': 1080}, {'width': 1366, 'height': 768}, {'width': 1536, 'height': 864}]
    
    browser = playwright_instance.chromium.launch(headless=True, proxy=get_proxy_settings())
    context = browser.new_context(
        viewport=random.choice(viewports),
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    
    page = context.new_page()

    def handle_response(response):
        nonlocal data
        # Capture any JSON response containing the marker to avoid missing dynamic URLs
        if conf["api_marker"] in response.url:
            try:
                data = response.json()
                print(f"✅ Successfully intercepted data for {site_id}")
            except:
                pass

    page.on("response", handle_response)
    
    try:
        # Increase timeout to 90s for high-latency residential proxies
        page.goto(conf["url"], wait_until="domcontentloaded", timeout=90000)
        # Extra dwell time to ensure background XHR requests fire
        time.sleep(random.uniform(10, 15)) 
    except Exception as e:
        print(f"⚠️ {site_id} timed out visually, checking for intercepted data...")

    if not data:
        # Final last-ditch wait for the specific API response
        try:
            page.wait_for_response(lambda r: conf["api_marker"] in r.url, timeout=20000)
        except:
            print(f"❌ No data captured for {site_id}")

    browser.close()
    return data

def send_status_report(results):
    status_webhook = os.getenv("DISCORD_STATUS_WEBHOOK_URL")
    if not status_webhook: return
    summary = "**📊 SCRAPER RUN STATUS REPORT**\n"
    for site, status in results.items():
        icon = "✅" if status == "success" else "❌"
        summary += f"{icon} **{site.upper()}**: {status}\n"
    post_discord({"content": summary}, webhook_url=status_webhook)

def run_pipeline():
    results = {}
    with Stealth().use_sync(sync_playwright()) as playwright:
        for sid in SITE_CONFIG.keys():
            try:
                raw = scrape_site(playwright, sid)
                if raw:
                    # In your actual script, call your process_alerts logic here
                    results[sid] = "success"
                else:
                    results[sid] = "failed (no data)"
            except Exception as e:
                results[sid] = f"error ({str(e)[:50]})"
            # Larger gap between sites to avoid IP rate-limiting
            time.sleep(random.uniform(8, 15))
    
    send_status_report(results)

if __name__ == "__main__":
    run_pipeline()
