import os
import random
import string
import time
from playwright.sync_api import sync_playwright

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord
from services.bet_logic import normalize_text

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
PROXY_USERNAME = os.getenv("PROXY_USERNAME")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD")
RAW_PROXY_LIST = os.getenv("PROXY_LIST", "")
PROXY_IPS = [ip.strip() for ip in RAW_PROXY_LIST.replace("\n", ",").split(",") if ip.strip()]

SITE_CONFIG = {
    "draftkings": {
        "url": "https://sportsbook.draftkings.com/leagues/basketball/nba",
        "api_marker": "api/sportscontent/v1/events/42648",
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
        "api_marker": "api.prizepicks.com/projections",
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
    browser = playwright.chromium.launch(headless=True, proxy=get_proxy())
    context = browser.new_context(ignore_https_errors=True, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    page = context.new_page()

    def handle_response(response):
        nonlocal data
        if conf["api_marker"] in response.url:
            try: data = response.json()
            except: pass

    page.on("response", handle_response)
    try:
        page.goto(conf["url"], wait_until="domcontentloaded", timeout=60000)
    except:
        pass

    if not data:
        try: page.wait_for_response(lambda r: conf["api_marker"] in r.url, timeout=15000)
        except: print(f"❌ Failed to intercept {site_id}")

    browser.close()
    return data

def process_alerts(site_id, raw_data):
    conf = SITE_CONFIG[site_id]
    prev_lines = load_tracker_state(conf["state_key"], conf["cache_file"])
    current_lines, alerts = {}, []

    if site_id == "prizepicks":
        players = {item["id"]: item["attributes"]["name"] for item in raw_data.get("included", []) if item.get("type") == "new_player"}
        for proj in raw_data.get("data", []):
            attr = proj.get("attributes", {})
            p_id = proj.get("relationships", {}).get("new_player", {}).get("data", {}).get("id")
            name, stat, line = players.get(p_id), attr.get("stat_type"), attr.get("line_score")
            if not name or line is None: continue
            key = f"{p_id}_{stat}"
            current_lines[key] = {"player": name, "line": line}
            if key in prev_lines and abs(float(line) - float(prev_lines[key]["line"])) >= 1.0:
                alerts.append(f"**PRIZEPICKS BUMP:** {name} {stat} {prev_lines[key]['line']} -> {line}")

    elif site_id == "betmgm":
        for fix in raw_data.get("fixtures", []):
            m_name = fix.get("name", {}).get("value")
            for market in fix.get("optionMarkets", []):
                if "Spread" not in market.get("name", {}).get("value", ""): continue
                for opt in market.get("options", []):
                    team, line = opt.get("name", {}).get("value"), opt.get("attributes", {}).get("spread")
                    if line is None: continue
                    key = f"{fix.get('id')}_{team}"
                    current_lines[key] = {"line": line}
                    if key in prev_lines and abs(float(line) - float(prev_lines[key]["line"])) >= 1.5:
                        alerts.append(f"**MGM STEAM:** {m_name} | {team} {prev_lines[key]['line']} -> {line}")

    elif site_id == "fanduel":
        markets, events = raw_data.get("attachments", {}).get("markets", {}), raw_data.get("attachments", {}).get("events", {})
        for m_id, m_data in markets.items():
            if m_data.get("marketName") != "Spread": continue
            m_name = events.get(str(m_data.get("eventId")), {}).get("name")
            for run in m_data.get("runners", []):
                team, line = run.get("runnerName"), run.get("handicap")
                if line is None: continue
                key = f"{m_data.get('eventId')}_{team}"
                current_lines[key] = {"line": line}
                if key in prev_lines and abs(float(line) - float(prev_lines[key]["line"])) >= 1.5:
                    alerts.append(f"**FD STEAM:** {m_name} | {team} {prev_lines[key]['line']} -> {line}")

    elif site_id == "draftkings":
        for ev in raw_data.get("events", []):
            m_name = ev.get("name")
            for mkt in ev.get("markets", []):
                if mkt.get("name") != "Spread": continue
                for out in mkt.get("outcomes", []):
                    team, line = out.get("label"), out.get("line")
                    if line is None: continue
                    key = f"{ev.get('id')}_{team}"
                    current_lines[key] = {"line": line}
                    if key in prev_lines and abs(float(line) - float(prev_lines[key]["line"])) >= 1.5:
                        alerts.append(f"**DK STEAM:** {m_name} | {team} {prev_lines[key]['line']} -> {line}")

    save_tracker_state(conf["state_key"], current_lines, conf["cache_file"])
    for msg in alerts:
        post_discord({"embeds": [{"description": msg, "color": conf["color"]}]}, webhook_url=DISCORD_WEBHOOK_URL)

def run_pipeline():
    with sync_playwright() as p:
        for sid in SITE_CONFIG.keys():
            raw = scrape_site(p, sid)
            if raw: process_alerts(sid, raw)
            time.sleep(5)

if __name__ == "__main__":
    run_pipeline()
