import json
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    from supabase import Client, create_client
except ImportError:
    Client = Any
    create_client = None

from utils.odds import american_to_decimal, parse_float
from utils.time import get_local_date_str, get_local_now


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if create_client and SUPABASE_URL and SUPABASE_KEY else None
RUNTIME_DB_STATS = {
    "bet_log_success": 0,
    "bet_log_failure": 0,
    "execution_log_success": 0,
    "execution_log_failure": 0,
}
PENDING_BETS_LOG_PATH = os.getenv("PENDING_BETS_LOG_PATH", "pending_bets_log.json")
PENDING_EXECUTION_REPORTS_PATH = os.getenv("PENDING_EXECUTION_REPORTS_PATH", "pending_execution_reports.json")


def _safe_execute(action, fallback):
    if not supabase:
        return fallback
    try:
        return action()
    except Exception as exc:
        print(f"Supabase operation failed: {exc}")
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
    if market_key.startswith("player_") or market_key in {"points", "assists", "rebounds", "goals"}:
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

    return _safe_execute(action, False)


def reset_runtime_db_stats():
    RUNTIME_DB_STATS["bet_log_success"] = 0
    RUNTIME_DB_STATS["bet_log_failure"] = 0
    RUNTIME_DB_STATS["execution_log_success"] = 0
    RUNTIME_DB_STATS["execution_log_failure"] = 0
    flush_pending_bet_logs()
    flush_pending_execution_reports()


def get_runtime_db_stats() -> Dict[str, int]:
    return dict(RUNTIME_DB_STATS)


def _queue_pending_bet_log(payload: Dict[str, Any]) -> None:
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
    logged_at = get_local_now().isoformat()

    order_payload = {
        "order_id": order_id,
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "quantity": order.get("quantity"),
        "limit_price": order.get("limit_price"),
        "fair_price": order.get("fair_price"),
        "order_type": order.get("order_type"),
        "time_in_force": order.get("time_in_force"),
        "strategy": order.get("strategy"),
        "source_signal": order.get("source_signal"),
        "status": safe_report.get("status"),
        "rejected_reason": safe_report.get("rejected_reason"),
        "filled_quantity": metrics.get("filled_quantity"),
        "fill_rate": metrics.get("fill_rate"),
        "average_price": metrics.get("average_price"),
        "slippage": metrics.get("slippage"),
        "edge_capture": metrics.get("edge_capture"),
        "fees": metrics.get("fees"),
        "metadata": order.get("metadata", {}),
        "created_at": order.get("created_at"),
        "logged_at": logged_at,
    }

    children_payload = []
    for child in child_orders:
        children_payload.append(
            {
                "child_order_id": child.get("child_order_id"),
                "parent_order_id": child.get("parent_order_id") or order_id,
                "venue_id": child.get("venue_id"),
                "symbol": child.get("symbol"),
                "side": child.get("side"),
                "quantity": child.get("quantity"),
                "limit_price": child.get("limit_price"),
                "route_score": child.get("route_score"),
                "status": child.get("status"),
                "metadata": child.get("metadata", {}),
                "logged_at": logged_at,
            }
        )

    fills_payload = []
    fills_by_child = {}
    for fill in fills:
        fill_id = f"{fill.get('child_order_id')}:{fill.get('filled_at')}"
        fills_by_child.setdefault(fill.get("child_order_id"), []).append(fill)
        fills_payload.append(
            {
                "fill_id": fill_id,
                "child_order_id": fill.get("child_order_id"),
                "parent_order_id": fill.get("parent_order_id") or order_id,
                "venue_id": fill.get("venue_id"),
                "symbol": fill.get("symbol"),
                "side": fill.get("side"),
                "quantity": fill.get("quantity"),
                "price": fill.get("price"),
                "fee": fill.get("fee"),
                "filled_at": fill.get("filled_at"),
            }
        )

    venue_metrics_payload = []
    for child in child_orders:
        child_fills = fills_by_child.get(child.get("child_order_id"), [])
        filled_qty = sum(float(fill.get("quantity") or 0) for fill in child_fills)
        notional = sum(float(fill.get("quantity") or 0) * float(fill.get("price") or 0) for fill in child_fills)
        fees = sum(float(fill.get("fee") or 0) for fill in child_fills)
        avg_price = (notional / filled_qty) if filled_qty else None
        metric_id = f"{child.get('child_order_id')}:{logged_at}"
        venue_metrics_payload.append(
            {
                "metric_id": metric_id,
                "parent_order_id": child.get("parent_order_id") or order_id,
                "child_order_id": child.get("child_order_id"),
                "venue_id": child.get("venue_id"),
                "symbol": child.get("symbol"),
                "status": child.get("status"),
                "routed_quantity": child.get("quantity"),
                "filled_quantity": filled_qty,
                "fill_rate": filled_qty / float(child.get("quantity") or 1),
                "average_fill_price": avg_price,
                "route_score": child.get("route_score"),
                "fee": fees,
                "latency_ms": child.get("metadata", {}).get("latency_ms"),
                "fill_probability": child.get("metadata", {}).get("fill_probability"),
                "edge_capture": metrics.get("edge_capture"),
                "measured_at": logged_at,
            }
        )

    return {
        "order": order_payload,
        "child_orders": children_payload,
        "fills": fills_payload,
        "venue_metrics": venue_metrics_payload,
    }


def _insert_execution_payload(payload: Dict[str, Any]) -> bool:
    def action():
        supabase.table("execution_orders").upsert(payload["order"]).execute()
        if payload["child_orders"]:
            supabase.table("execution_child_orders").upsert(payload["child_orders"]).execute()
        if payload["fills"]:
            supabase.table("execution_fills").upsert(payload["fills"]).execute()
        if payload["venue_metrics"]:
            supabase.table("venue_metrics").upsert(payload["venue_metrics"]).execute()
        return True

    return _safe_execute(action, False)


def log_execution_report_to_db(report: Dict[str, Any]) -> bool:
    payload = _execution_payload_from_report(report)
    if not supabase:
        RUNTIME_DB_STATS["execution_log_failure"] += 1
        _queue_pending_execution_report(payload)
        return False

    success = _insert_execution_payload(payload)
    if success:
        RUNTIME_DB_STATS["execution_log_success"] += 1
    else:
        RUNTIME_DB_STATS["execution_log_failure"] += 1
        _queue_pending_execution_report(payload)
    return success


def get_venue_metrics(limit: int = 500) -> List[Dict[str, Any]]:
    def action():
        return (
            supabase.table("venue_metrics")
            .select("*")
            .order("measured_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )

    return _safe_execute(action, [])


def get_table_count(table_name: str) -> int:
    def action():
        response = supabase.table(table_name).select("*", count="exact").limit(0).execute()
        return int(response.count or 0)

    return _safe_execute(action, 0)


def get_latest_rows(table_name: str, order_column: str, limit: int = 5) -> List[Dict[str, Any]]:
    def action():
        return (
            supabase.table(table_name)
            .select("*")
            .order(order_column, desc=True)
            .limit(limit)
            .execute()
            .data
        )

    return _safe_execute(action, [])


def log_bet_to_db(
    matchup,
    market,
    selection,
    odds,
    edge_val,
    units,
    fair_price,
    sport,
    event_id,
    bet_source: Optional[str] = None,
    notes: Optional[str] = None,
):
    edge_pct = _parse_edge_pct(edge_val)
    if edge_pct is not None and edge_pct <= 0:
        print(f"Skipping non-positive edge bet log for {selection}: {edge_pct:.4f}%")
        RUNTIME_DB_STATS["bet_log_failure"] += 1
        return False

    odds_decimal = _parse_decimal_odds(odds)
    fair_price_decimal = _parse_decimal_odds(fair_price)
    payload = {
        "date": get_local_date_str(),
        "matchup": matchup.strip(),
        "market": market.upper().strip(),
        "selection": selection.strip(),
        "odds": str(odds),
        "edge": str(edge_val if isinstance(edge_val, str) else f"{float(edge_val) * 100:.2f}%"),
        "units": str(units),
        "fair_price": str(fair_price),
        "sport": sport,
        "event_id": str(event_id),
        "closing_line_pinnacle": "",
        "result": "",
        "edge_pct": edge_pct,
        "odds_decimal": odds_decimal,
        "fair_price_decimal": fair_price_decimal,
        "bet_source": bet_source or _infer_bet_source(market, sport),
        "market_type": _infer_market_type(market),
        "notes": notes,
    }

    if not supabase:
        RUNTIME_DB_STATS["bet_log_failure"] += 1
        _queue_pending_bet_log(payload)
        return False

    legacy_payload = {
        key: payload[key]
        for key in (
            "date",
            "matchup",
            "market",
            "selection",
            "odds",
            "edge",
            "units",
            "fair_price",
            "sport",
            "event_id",
            "closing_line_pinnacle",
            "result",
        )
    }

    def action():
        try:
            supabase.table("bets_log").insert(payload).execute()
        except Exception as exc:
            print(f"Full bets_log insert failed, retrying legacy payload: {exc}")
            supabase.table("bets_log").insert(legacy_payload).execute()
        return True

    success = _safe_execute(action, False)
    if success:
        RUNTIME_DB_STATS["bet_log_success"] += 1
    else:
        RUNTIME_DB_STATS["bet_log_failure"] += 1
        _queue_pending_bet_log(payload)
    return success


def get_ungraded_past_bets() -> List[Dict[str, Any]]:
    today = get_local_date_str()

    def action():
        return supabase.table("bets_log").select("*").eq("result", "").lt("date", today).execute().data

    return _safe_execute(action, [])


def get_untracked_bets() -> List[Dict[str, Any]]:
    def action():
        rows = supabase.table("bets_log").select("*").execute().data
        return [row for row in rows if not row.get("closing_line_pinnacle")]

    return _safe_execute(action, [])


def get_all_bets() -> List[Dict[str, Any]]:
    def action():
        return supabase.table("bets_log").select("*").execute().data

    return _safe_execute(action, [])


def get_all_graded_bets() -> List[Dict[str, Any]]:
    def action():
        return supabase.table("bets_log").select("*").neq("result", "").execute().data

    return _safe_execute(action, [])


def get_all_clv_bets() -> List[Dict[str, Any]]:
    def action():
        rows = supabase.table("bets_log").select("*").execute().data
        return [row for row in rows if row.get("closing_line_decimal") or row.get("closing_line_pinnacle")]

    return _safe_execute(action, [])


def update_result(bet_id, result):
    def action():
        supabase.table("bets_log").update(
            {"result": result, "graded_at": get_local_now().isoformat()}
        ).eq("id", bet_id).execute()

    _safe_execute(action, None)


def update_bet_clv(bet_id, closing_price_american, closing_price_decimal, clv_edge_pct: Optional[float] = None):
    update_payload = {
        "closing_line_pinnacle": str(closing_price_american),
        "closing_line_american": str(closing_price_american),
        "closing_line_decimal": closing_price_decimal,
        "clv_tracked_at": get_local_now().isoformat(),
    }
    if clv_edge_pct is not None:
        update_payload["clv_edge_pct"] = clv_edge_pct

    def action():
        supabase.table("bets_log").update(update_payload).eq("id", bet_id).execute()

    _safe_execute(action, None)


def save_odds_cache(cache_data):
    def action():
        supabase.table("odds_cache").upsert(
            {"id": "master", "data": cache_data, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).execute()

    _safe_execute(action, None)


def get_odds_cache():
    def action():
        response = supabase.table("odds_cache").select("data").eq("id", "master").execute()
        return response.data[0]["data"] if response.data else {}

    return _safe_execute(action, {})


def save_master_cache(cache_data):
    save_odds_cache(cache_data)


def get_master_cache():
    return get_odds_cache()


def load_tracker_state(state_key: str, fallback_path: str):
    def action():
        response = supabase.table("bot_state").select("data").eq("id", state_key).execute()
        if response.data:
            return response.data[0].get("data", {})
        return _load_local_json(fallback_path, {})

    return _safe_execute(action, _load_local_json(fallback_path, {}))


def save_tracker_state(state_key: str, data, fallback_path: str) -> None:
    _save_local_json(fallback_path, data)

    def action():
        supabase.table("bot_state").upsert(
            {"id": state_key, "data": data, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).execute()

    _safe_execute(action, None)


def log_alert_event(
    source: str,
    alert_type: str,
    dedupe_key: Optional[str] = None,
    count: int = 1,
    payload_preview: Optional[str] = None,
    status: str = "sent",
):
    def action():
        supabase.table("alerts_sent").insert(
            {
                "source": source,
                "alert_type": alert_type,
                "dedupe_key": dedupe_key,
                "count": count,
                "payload_preview": payload_preview,
                "status": status,
                "sent_at": get_local_now().isoformat(),
            }
        ).execute()

    _safe_execute(action, None)


def log_workflow_run(
    workflow_name: str,
    status: str,
    runtime_seconds: float,
    task_count: int,
    failure_count: int,
    alert_count: int,
    graded_count: int,
    tracked_count: int,
    summary: Optional[str] = None,
):
    def action():
        supabase.table("workflow_runs").insert(
            {
                "workflow_name": workflow_name,
                "status": status,
                "runtime_seconds": runtime_seconds,
                "task_count": task_count,
                "failure_count": failure_count,
                "alert_count": alert_count,
                "graded_count": graded_count,
                "tracked_count": tracked_count,
                "summary": summary,
                "run_at": get_local_now().isoformat(),
            }
        ).execute()

    _safe_execute(action, None)
