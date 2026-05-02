import os
from typing import Dict

from db_manager import load_tracker_state, save_tracker_state
from services.http_client import post_discord, request


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TRACKER_FILE = "prizepicks_lines.json"
STATE_KEY = "tracker_prizepicks_nba"
PRIZEPICKS_LEAGUE_ID = os.getenv("PRIZEPICKS_NBA_LEAGUE_ID", "7")
PRIZEPICKS_URL = "https://api.prizepicks.com/projections"
PRIZEPICKS_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://app.prizepicks.com",
    "Referer": "https://app.prizepicks.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def load_previous_lines():
    return load_tracker_state(STATE_KEY, TRACKER_FILE)


def save_current_lines(lines):
    save_tracker_state(STATE_KEY, lines, TRACKER_FILE)


def _fetch_prizepicks_data() -> dict:
    response = request(
        "GET",
        PRIZEPICKS_URL,
        params={"league_id": PRIZEPICKS_LEAGUE_ID, "per_page": 250, "single_stat": "true"},
        headers=PRIZEPICKS_HEADERS,
        timeout=20,
        retry_on_429=False,
    )
    return response.json()


def _player_map(payload: dict) -> Dict[str, str]:
    players = {}
    for item in payload.get("included", []):
        if item.get("type") == "new_player":
            players[str(item.get("id"))] = item.get("attributes", {}).get("name", "Unknown Player")
    return players


def scrape_prizepicks():
    try:
        data = _fetch_prizepicks_data()
    except Exception as exc:
        print(f"PrizePicks direct fetch failed: {exc}")
        return {"detail": f"prizepicks scrape error: {exc}", "count": 0, "label": "alerts"}

    if "data" not in data:
        print("PrizePicks direct fetch returned no projection payload.")
        return {"detail": "prizepicks scrape no data", "count": 0, "label": "alerts"}

    current_lines = {}
    alerts = []
    previous_lines = load_previous_lines()
    players = _player_map(data)

    for projection in data.get("data", []):
        if projection.get("type") != "projection":
            continue

        attributes = projection.get("attributes", {})
        stat_type = attributes.get("stat_type")
        line = attributes.get("line_score")
        player_id = str(
            projection.get("relationships", {})
            .get("new_player", {})
            .get("data", {})
            .get("id", "")
        )
        player_name = players.get(player_id, "Unknown Player")
        if line is None or player_name == "Unknown Player" or not stat_type:
            continue

        unique_key = f"{player_id}_{stat_type}"
        current_lines[unique_key] = {"player": player_name, "stat": stat_type, "line": line}
        if unique_key not in previous_lines:
            continue

        old_line = previous_lines[unique_key]["line"]
        diff = abs(float(line) - float(old_line))
        if diff >= 1.0:
            alerts.append(
                f"**PRIZEPICKS BUMP ALERT:** {player_name}\n"
                f"**{stat_type} Moved!**\n"
                f"Old Line: {old_line} -> **New Line: {line}**"
            )

    save_current_lines(current_lines)
    for message in alerts[:5]:
        post_discord({"embeds": [{"description": message, "color": 10181046}]}, webhook_url=DISCORD_WEBHOOK_URL)
    return {
        "detail": f"prizepicks scrape complete ({len(current_lines)} lines tracked)",
        "count": min(len(alerts), 5),
        "label": "alerts",
    }


def scrape_pp():
    return scrape_prizepicks()


if __name__ == "__main__":
    scrape_prizepicks()
