import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from utils.odds import american_to_decimal, parse_float
from utils.time import get_local_date_str, get_local_now


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None


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
        response = (
            supabase.table("bets_log")
            .select("id")
            .ilike("matchup", matchup.strip())
            .eq("market", market.strip().upper())
            .ilike("selection", selection.strip())
            .eq("result", "")
            .execute()
        )
        return len(response.data) > 0

    return _safe_execute(action, False)


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
    if not supabase:
        return

    edge_pct = _parse_edge_pct(edge_val)
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

    def action():
        supabase.table("bets_log").insert(payload).execute()

    _safe_execute(action, None)


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


def save_master_cache(cache_data):
    def action():
        supabase.table("odds_cache").upsert(
            {"id": "master", "data": cache_data, "updated_at": datetime.utcnow().isoformat()}
        ).execute()

    _safe_execute(action, None)


def get_master_cache():
    def action():
        response = supabase.table("odds_cache").select("data").eq("id", "master").execute()
        return response.data[0]["data"] if response.data else {}

    return _safe_execute(action, {})


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
            {"id": state_key, "data": data, "updated_at": datetime.utcnow().isoformat()}
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
