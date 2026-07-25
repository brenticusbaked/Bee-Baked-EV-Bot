from datetime import datetime, timezone

from db_manager import get_master_cache, load_tracker_state, save_tracker_state
from master_odds_fetcher import run_fetcher
from services.discord_channels import STATUS_WEBHOOK_URL
from services.http_client import post_discord
from services.live_edges import find_live_edge_alerts, send_live_edge_alerts, summarize_live_edge_alerts
from utils.config import env_flag
from utils.time import get_local_date_str


STATE_KEY = "live_edge_poll"
STATE_FILE = "live_edge_poll_state.json"
MAX_DEDUPE_KEYS = 500


def _load_dedupe_keys() -> set[str]:
    state = load_tracker_state(STATE_KEY, STATE_FILE)
    if not isinstance(state, dict) or state.get("date") != get_local_date_str():
        return set()
    return set(state.get("dedupe_keys", []))


def _save_dedupe_keys(keys: set[str]) -> None:
    save_tracker_state(
        STATE_KEY,
        {"date": get_local_date_str(), "dedupe_keys": sorted(keys)[-MAX_DEDUPE_KEYS:]},
        STATE_FILE,
    )


def _send_status(fetch_detail: str, alert_count: int, sent_count: int, cache_sports: int, alert_summary: str) -> None:
    if not STATUS_WEBHOOK_URL or not env_flag("LIVE_EDGE_STATUS_NOTIFY", False):
        return
    post_discord(
        {
            "embeds": [
                {
                    "description": (
                        "**LIVE EDGE WINDOW**\n"
                        f"Fetch: {fetch_detail}\n"
                        f"Sports cached: {cache_sports}\n"
                        f"Live alerts sent: {sent_count}/{alert_count}\n"
                        f"Scan: {alert_summary}"
                    ),
                    "color": 5763719,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ]
        },
        webhook_url=STATUS_WEBHOOK_URL,
    )


def run_live_edge_poll() -> dict:
    previous_cache = get_master_cache() or {}
    fetch_result = run_fetcher()
    current_cache = get_master_cache() or {}
    sent_dedupe_keys = _load_dedupe_keys()

    alerts = find_live_edge_alerts(previous_cache, current_cache, current_cache)
    sent = send_live_edge_alerts(alerts, sent_dedupe_keys=sent_dedupe_keys)
    _save_dedupe_keys(sent_dedupe_keys)

    fetch_detail = fetch_result.get("detail", "fetch complete") if isinstance(fetch_result, dict) else str(fetch_result)
    alert_summary = summarize_live_edge_alerts(alerts)
    _send_status(fetch_detail, len(alerts), sent, len(current_cache), alert_summary)
    return {
        "detail": f"live edge poll complete | {fetch_detail}",
        "count": sent,
        "label": "alerts",
        "meta": {"alert_candidates": str(len(alerts)), "alert_summary": alert_summary},
    }


if __name__ == "__main__":
    run_live_edge_poll()
