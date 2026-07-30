import json
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Load local environment variables for manual runs
load_dotenv()

try:
    from supabase import Client, create_client
except ImportError:
    Client = Any
    create_client = None

from utils.odds import american_to_decimal
from utils.time import get_local_date_str, get_local_now

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_DEAD_LETTER_WEBHOOK_URL = os.getenv("DISCORD_DEAD_LETTER_WEBHOOK_URL") or os.getenv("DISCORD_STATUS_WEBHOOK_URL")

def _force_postgrest_http1(client: "Client") -> None:
    try:
        import httpx

        session = getattr(client.postgrest, "session", None)
        if not isinstance(session, httpx.Client):
            return
        http1_session = httpx.Client(
            base_url=session.base_url,
            headers=session.headers,
            timeout=session.timeout,
            follow_redirects=session.follow_redirects,
            http2=False,
        )
        client.postgrest.session = http1_session
    except Exception as exc:
        print(f"[supabase] Could not force HTTP/1.1 on PostgREST session: {exc}")


supabase: Client = None
if create_client and SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        _force_postgrest_http1(supabase)
    except Exception as exc:
        print(f"[supabase] Failed to create client: {exc}")
        supabase = None
else:
    _missing = []
    if not create_client:
        _missing.append("supabase library")
    if not SUPABASE_URL:
        _missing.append("SUPABASE_URL")
    if not SUPABASE_KEY:
        _missing.append("SUPABASE_KEY")
    print(f"[supabase] Client disabled missing: {', '.join(_missing)}")

SUPABASE_REACHABLE: bool = bool(supabase)

RUNTIME_DB_STATS = {
    "bet_log_success": 0,
    "bet_log_failure": 0,
    "execution_log_success": 0,
    "execution_log_failure": 0,
}

PENDING_BETS_LOG_PATH = os.getenv("PENDING_BETS_LOG_PATH", "pending_bets_log.json")
PENDING_EXECUTION_REPORTS_PATH = os.getenv("PENDING_EXECUTION_REPORTS_PATH", "pending_execution_reports.json")
LOCAL_ODDS_CACHE_PATH = os.getenv("LOCAL_ODDS_CACHE_PATH", "odds_cache.json")

def _safe_execute(action, fallback):
    global SUPABASE_REACHABLE
    if not supabase:
        SUPABASE_REACHABLE = False
        return fallback
    try:
        return action()
    except Exception as exc:
        import traceback
        print(f"[supabase] Operation failed: {exc}")
        print(f"[supabase] Traceback: {traceback.format_exc()}")
        exc_text = str(exc)
        if (
            "ConnectError" in exc_text
            or "[Errno -2]" in exc_text
            or "Name or service not known" in exc_text
            or "getaddrinfo failed" in exc_text
        ):
            SUPABASE_REACHABLE = False
        return fallback

def _load_local_json(path: str, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        print(f"Local JSON load failed for {path}: {exc}")
        return default

def _save_local_json(path: str, data) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except Exception as exc:
        print(f"Local JSON save failed for {path}: {exc}")

def _json_safe(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value

def _parse_decimal_odds(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        text = str(value).strip()
        if text.startswith("+") or text.startswith("-"):
            return american_to_decimal(text)
        numeric = float(text)
        return numeric if numeric > 1.0 else None
    except Exception:
        return None

def _parse_edge_pct(edge_val) -> Optional[float]:
    if edge_val is None or edge_val == "":
        return None
    if isinstance(edge_val, str):
        cleaned = edge_val.replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    try:
        return float(edge_val) * 100.0
    except (TypeError, ValueError):
        return None

def _infer_market_type(market: str) -> str:
    market_key = str(market).strip().lower()
    if market_key in {"h2h", "moneyline", "model_mlb_f5"}:
        return "moneyline"
    if market_key in {"spread", "spreads", "model_nba_spread", "model_nhl_puckline", "puckline"}:
        return "spread"
    if market_key in {"total", "totals"}:
        return "total"
    if market_key.startswith("player_") or market_key in {"points", "assists", "rebounds", "goals", "hits", "total_bases"}:
        return "player_prop"
    return "other"

def _infer_bet_source(market: str, sport: str) -> str:
    market_key = str(market).strip().upper()
    if market_key.startswith("MODEL_"):
        return "model"
    if sport == "unknown_scraped":
        return "scraper"
    return "market_scan"

def is_already_logged(matchup, market, selection):
    def action():
        rows = (
            supabase.table("bets_log")
            .select("id,result")
            .ilike("matchup", matchup.strip())
            .eq("market", market.strip().upper())
            .ilike("selection", selection.strip())
            .execute()
            .data
        )
        return any(not row.get("result") for row in rows)

    if _safe_execute(action, False):
        return True

    today = get_local_date_str()
    pending = _load_local_json(PENDING_BETS_LOG_PATH, [])
    if not isinstance(pending, list):
        return False
    market_key = market.strip().upper()
    matchup_key = matchup.strip().lower()
    selection_key = selection.strip().lower()
    for row in pending:
        if (
            row.get("date") == today
            and str(row.get("matchup", "")).strip().lower() == matchup_key
            and str(row.get("market", "")).strip().upper() == market_key
            and str(row.get("selection", "")).strip().lower() == selection_key
            and not row.get("result")
        ):
            return True
    return False

REQUIRED_TABLES = [
    "bets_log",
    "odds_cache",
    "bot_state",
    "alerts_sent",
    "workflow_runs",
    "execution_orders",
    "execution_child_orders",
    "execution_fills",
    "venue_metrics",
    "fixtures",
    "historical_odds",
]

def validate_supabase_connection() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "connected": False,
        "tables": {},
        "errors": [],
    }
    if not supabase:
        result["errors"].append("Supabase client not initialised (missing URL/KEY or library)")
        return result
    try:
        supabase.table("bets_log").select("id", count="exact").limit(0).execute()
        result["connected"] = True
    except Exception as exc:
        result["errors"].append(f"Connection test failed: {exc}")
        return result
    for table_name in REQUIRED_TABLES:
        try:
            supabase.table(table_name).select("*", count="exact").limit(0).execute()
            result["tables"][table_name] = True
        except Exception as exc:
            result["tables"][table_name] = False
            result["errors"].append(f"Table '{table_name}' not accessible: {exc}")
    missing = [t for t, ok in result["tables"].items() if not ok]
    result["ok"] = result["connected"] and len(missing) == 0
    if missing:
        print(f"[supabase] WARNING: Missing or inaccessible tables: {', '.join(missing)}")
        print("[supabase] Run supabase_core_schema.sql and supabase_execution_schema.sql in the SQL editor.")
    else:
        print(f"[supabase] All {len(REQUIRED_TABLES)} required tables verified.")
    return result

def reset_runtime_db_stats():
    RUNTIME_DB_STATS["bet_log_success"] = 0
    RUNTIME_DB_STATS["bet_log_failure"] = 0
    RUNTIME_DB_STATS["execution_log_success"] = 0
    RUNTIME_DB_STATS["execution_log_failure"] = 0
    flush_pending_bet_logs()
    flush_pending_execution_reports()

def get_runtime_db_stats() -> Dict[str, int]:
    return dict(RUNTIME_DB_STATS)

def _send_dead_letter(table_name: str, payload: dict) -> None:
    if not DISCORD_DEAD_LETTER_WEBHOOK_URL:
        return
    try:
        from services.http_client import post_discord
        
        safe_payload = json.dumps(payload, indent=2, default=str)[:1850]
        cb = "```"
        message = f"**🚨 CRITICAL: SUPABASE INSERT FAILED 🚨**\n**Table:** `{table_name}`\n**Payload:**\n{cb}json\n{safe_payload}\n{cb}"

        post_discord({"content": message}, webhook_url=DISCORD_DEAD_LETTER_WEBHOOK_URL)
    except Exception as exc:
        print(f"Failed to dispatch dead-letter to Discord: {exc}")

def _queue_pending_bet_log(payload: Dict[str, Any], alert: bool = True) -> None:
    if alert and supabase is not None and SUPABASE_REACHABLE:
        _send_dead_letter("bets_log", payload)
    
    pending = _load_local_json(PENDING_BETS_LOG_PATH, [])
    if not isinstance(pending, list):
        pending = []
    identity = (
        payload.get("date"),
        payload.get("matchup"),
        payload.get("market"),
        payload.get("selection"),
        payload.get("event_id"),
    )
    for row in pending:
        if (
            row.get("date"),
            row.get("matchup"),
            row.get("market"),
            row.get("selection"),
            row.get("event_id"),
        ) == identity:
            return
    pending.append(payload)
    _save_local_json(PENDING_BETS_LOG_PATH, pending)

def flush_pending_bet_logs() -> int:
    if not supabase:
        return 0
    pending = _load_local_json(PENDING_BETS_LOG_PATH, [])
    if not isinstance(pending, list) or not pending:
        return 0
    remaining = []
    flushed = 0
    for payload in pending:
        def action():
            supabase.table("bets_log").insert(payload).execute()
            return True
        if _safe_execute(action, False):
            flushed += 1
        else:
            remaining.append(payload)
    _save_local_json(PENDING_BETS_LOG_PATH, remaining)
    if flushed:
        print(f"Flushed {flushed} pending bet log(s) to Supabase.")
    return flushed

def _queue_pending_execution_report(payload: Dict[str, Any]) -> None:
    _send_dead_letter("execution_orders", payload)
    
    pending = _load_local_json(PENDING_EXECUTION_REPORTS_PATH, [])
    if not isinstance(pending, list):
        pending = []
    order_id = payload.get("order", {}).get("order_id")
    for row in pending:
        if row.get("order", {}).get("order_id") == order_id:
            return
    pending.append(_json_safe(payload))
    _save_local_json(PENDING_EXECUTION_REPORTS_PATH, pending)

def flush_pending_execution_reports() -> int:
    if not supabase:
        return 0
    pending = _load_local_json(PENDING_EXECUTION_REPORTS_PATH, [])
    if not isinstance(pending, list) or not pending:
        return 0
    remaining = []
    flushed = 0
    for payload in pending:
        if _insert_execution_payload(payload):
            flushed += 1
        else:
            remaining.append(payload)
    _save_local_json(PENDING_EXECUTION_REPORTS_PATH, remaining)
    if flushed:
        print(f"Flushed {flushed} pending execution report(s) to Supabase.")
    return flushed

def _execution_payload_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    safe_report = _json_safe(report)
    order = safe_report.get("parent_order", {})
    child_orders = safe_report.get("child_orders", [])
    fills = safe_report.get("fills", [])
    metrics = safe_report.get("metrics", {})
    order_id = order.get("order_id")
    logged_at = get_local_now().isoformat