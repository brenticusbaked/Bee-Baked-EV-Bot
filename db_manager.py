import json
import os
from datetime import datetime
from typing import Any, Dict, List

from supabase import Client, create_client

from utils.time import get_local_date_str


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


def log_bet_to_db(matchup, market, selection, odds, edge_val, units, fair_price, sport, event_id):
    if not supabase:
        return

    edge_text = edge_val if isinstance(edge_val, str) else f"{float(edge_val) * 100:.2f}%"
    payload = {
        "date": get_local_date_str(),
        "matchup": matchup.strip(),
        "market": market.upper().strip(),
        "selection": selection.strip(),
        "odds": str(odds),
        "edge": str(edge_text),
        "units": str(units),
        "fair_price": str(fair_price),
        "sport": sport,
        "event_id": str(event_id),
        "closing_line_pinnacle": "",
        "result": "",
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
        return supabase.table("bets_log").select("*").eq("closing_line_pinnacle", "").execute().data

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
        return supabase.table("bets_log").select("*").neq("closing_line_pinnacle", "").execute().data

    return _safe_execute(action, [])


def update_result(bet_id, result):
    def action():
        supabase.table("bets_log").update({"result": result}).eq("id", bet_id).execute()

    _safe_execute(action, None)


def update_bet_clv(bet_id, closing_price):
    def action():
        supabase.table("bets_log").update({"closing_line_pinnacle": str(closing_price)}).eq("id", bet_id).execute()

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
