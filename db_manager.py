import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger("db_manager")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.warning(f"Could not initialize Supabase client: {e}")
else:
    logger.info("[supabase] Client disabled missing: SUPABASE_URL, SUPABASE_KEY")

REQUIRED_TABLES = ["bets_log", "odds_cache", "market_cache", "tracker_state", "alert_events"]


def _safe_execute(action, fallback):
    if not supabase:
        return fallback
    try:
        return action()
    except Exception as e:
        logger.error(f"Supabase execution error: {e}")
        return fallback


def validate_supabase_connection() -> bool:
    if not supabase:
        return False
    try:
        res = supabase.table("bets_log").select("id").limit(1).execute()
        return res is not None
    except Exception as e:
        logger.error(f"Supabase validation failed: {e}")
        return False


def _force_postgrest_http1(client):
    try:
        if hasattr(client, "postgrest") and hasattr(client.postgrest, "session"):
            session = client.postgrest.session
            if hasattr(session, "_transport") and hasattr(session._transport, "_pool"):
                pool = session._transport._pool
                if hasattr(pool, "_http2"):
                    pool._http2 = False
            if hasattr(session, "headers"):
                session.headers.update({"Connection": "close"})
    except Exception:
        pass


def get_market_cache(max_age_minutes: Optional[int] = None) -> Dict[str, Any]:
    def action():
        res = supabase.table("market_cache").select("*").execute()
        data = res.data or {}
        if isinstance(data, list) and len(data) > 0 and "payload" in data[0]:
            return data[0]["payload"]
        return data
    return _safe_execute(action, {})


def save_market_cache(cache_data: Dict[str, Any]):
    def action():
        supabase.table("market_cache").upsert({"id": 1, "payload": cache_data, "updated_at": datetime.now(timezone.utc).isoformat()}).execute()
    _safe_execute(action, None)


def get_master_cache() -> Dict[str, Any]:
    def action():
        res = supabase.table("odds_cache").select("*").execute()
        data = res.data or {}
        if isinstance(data, list) and len(data) > 0 and "payload" in data[0]:
            return data[0]["payload"]
        return data
    return _safe_execute(action, {})


def save_master_cache(cache_data: Dict[str, Any]):
    def action():
        supabase.table("odds_cache").upsert({"id": 1, "payload": cache_data, "updated_at": datetime.now(timezone.utc).isoformat()}).execute()
    _safe_execute(action, None)


def get_all_bets() -> List[Dict[str, Any]]:
    def action():
        res = supabase.table("bets_log").select("*").order("created_at", desc=True).execute()
        return res.data or []
    return _safe_execute(action, [])


def get_today_bets() -> List[Dict[str, Any]]:
    def action():
        today = datetime.now(timezone.utc).date().isoformat()
        res = supabase.table("bets_log").select("*").gte("created_at", f"{today}T00:00:00").execute()
        return res.data or []
    return _safe_execute(action, [])


def get_all_graded_bets() -> List[Dict[str, Any]]:
    def action():
        res = supabase.table("bets_log").select("*").not_.is_("result", "null").execute()
        return res.data or []
    return _safe_execute(action, [])


def log_bet_to_db(*args, **kwargs) -> bool:
    bet_data = args[0] if args else kwargs
    def action():
        payload = dict(bet_data)
        if "created_at" not in payload:
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        res = supabase.table("bets_log").insert(payload).execute()
        return res is not None
    return _safe_execute(action, True)


def is_already_logged(sport: str, event_id: str, market: str, selection: str) -> bool:
    def action():
        res = (supabase.table("bets_log")
               .select("id")
               .eq("sport", sport)
               .eq("event_id", event_id)
               .eq("market", market)
               .eq("selection", selection)
               .execute())
        return len(res.data or []) > 0
    return _safe_execute(action, False)


def update_bet_clv(bet_id: Any, closing_odds: Any, clv_pct: Any, closing_line: Any = None):
    def action():
        update_data = {"closing_odds": closing_odds, "clv_pct": clv_pct}
        if closing_line is not None:
            update_data["closing_line"] = closing_line
        supabase.table("bets_log").update(update_data).eq("id", bet_id).execute()
    _safe_execute(action, None)


def load_tracker_state(sport: str, fallback: Dict[str, Any] = None) -> Dict[str, Any]:
    def action():
        res = supabase.table("tracker_state").select("state").eq("sport", sport).execute()
        if res.data and len(res.data) > 0:
            return res.data[0].get("state", fallback or {})
        return fallback or {}
    return _safe_execute(action, fallback or {})


def save_tracker_state(sport: str, data: Dict[str, Any], fallback: Dict[str, Any] = None):
    def action():
        supabase.table("tracker_state").upsert({"sport": sport, "state": data, "updated_at": datetime.now(timezone.utc).isoformat()}).execute()
    _safe_execute(action, None)


def log_alert_event(sport: str, alert: Dict[str, Any], dedupe_key: str = None, count: int = 1, payload_preview: Dict[str, Any] = None, status: str = 'sent'):
    def action():
        payload = {
            "sport": sport,
            "alert": alert,
            "dedupe_key": dedupe_key,
            "count": count,
            "payload_preview": payload_preview,
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        supabase.table("alert_events").insert(payload).execute()
    _safe_execute(action, None)


def alert_already_sent(alert_type: str, dedupe_key: str, window_hours: int = 24) -> bool:
    def action():
        res = (supabase.table("alert_events")
               .select("id")
               .eq("sport", alert_type)
               .eq("dedupe_key", dedupe_key)
               .execute())
        return len(res.data or []) > 0
    return _safe_execute(action, False)


def get_venue_metrics(limit: int = 500) -> List[Dict[str, Any]]:
    def action():
        res = supabase.table("venue_metrics").select("*").limit(limit).execute()
        return res.data or []
    return _safe_execute(action, [])


def log_execution_report_to_db(report: Dict[str, Any]) -> bool:
    def action():
        payload = dict(report)
        if "created_at" not in payload:
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("execution_reports").insert(payload).execute()
        return True
    return _safe_execute(action, False)


def _execution_payload_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    return dict(report)


def get_latest_rows(table: str, column: str, limit: int = 5) -> List[Dict[str, Any]]:
    def action():
        res = supabase.table(table).select("*").order(column, desc=True).limit(limit).execute()
        return res.data or []
    return _safe_execute(action, [])


def get_table_count(table: str) -> int:
    def action():
        res = supabase.table(table).select("id", count="exact").execute()
        return res.count or 0
    return _safe_execute(action, 0)


def assemble_cache(fetch_func=None, odds_data=None) -> Dict[str, Any]:
    if odds_data:
        if isinstance(odds_data, list):
            grouped = {}
            for row in odds_data:
                sport = row.get("sport_key")
                if not sport:
                    continue
                if sport not in grouped:
                    grouped[sport] = []
                
                fixture_id = row.get("fixture_id")
                # Group by event/fixture ID if present to build nested bookmaker/market structure
                existing_event = next((e for e in grouped[sport] if e.get("id") == fixture_id), None)
                if not existing_event:
                    existing_event = {
                        "id": fixture_id,
                        "sport_key": sport,
                        "bookmakers": []
                    }
                    grouped[sport].append(existing_event)
                
                book_key = row.get("bookmaker_key")
                existing_book = next((b for b in existing_event["bookmakers"] if b.get("key") == book_key), None)
                if not existing_book:
                    existing_book = {
                        "key": book_key,
                        "title": row.get("bookmaker_title"),
                        "markets": []
                    }
                    existing_event["bookmakers"].append(existing_book)
                
                market_key = row.get("market_key")
                existing_market = next((m for m in existing_book["markets"] if m.get("key") == market_key), None)
                if not existing_market:
                    existing_market = {
                        "key": market_key,
                        "outcomes": []
                    }
                    existing_book["markets"].append(existing_market)
                
                existing_market["outcomes"].append({
                    "name": row.get("outcome_name"),
                    "description": row.get("outcome_description"),
                    "point": row.get("point"),
                    "price": row.get("price_decimal"),
                    "last_update": row.get("last_update")
                })
            return grouped
        return odds_data
    if fetch_func:
        return fetch_func()
    return {}


def _stat_log_table(sport: str) -> Optional[str]:
    mapping = {
        "baseball_mlb": "mlb_player_logs",
        "basketball_nba": "nba_player_logs",
        "football_nfl": "nfl_player_logs",
        "icehockey_nhl": "nhl_player_logs",
        "basketball_wnba": "wnba_player_logs",
        "tennis_atp": "tennis_match_logs",
        "tennis_wta": "tennis_match_logs"
    }
    if sport in mapping:
        return mapping[sport]
    if sport.startswith("soccer_"):
        return "soccer_player_logs"
    return None


def _stat_value_for_prop(row: dict, prop: str) -> Optional[float]:
    if prop in row and row[prop] is not None:
        try:
            return float(row[prop])
        except (TypeError, ValueError):
            pass
    for k, v in row.items():
        if k.lower() == prop.lower() and v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    stats = row.get("stats")
    if isinstance(stats, dict):
        if prop in stats and stats[prop] is not None:
            try:
                return float(stats[prop])
            except (TypeError, ValueError):
                pass
        for k, v in stats.items():
            if k.lower() == prop.lower() and v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
    return None


def get_l10_hit_rate(player: str, prop: str, line: float, sport: str, games: int = 10) -> Optional[Dict[str, Any]]:
    table = _stat_log_table(sport)
    if not table:
        return None
    try:
        line_value = float(line)
    except (TypeError, ValueError):
        return None
    
    def action():
        return (
            supabase.table(table)
            .select("*")
            .ilike("player_name", player)
            .order("game_date", desc=True)
            .limit(max(1, int(games)))
            .execute()
            .data
        )

    rows = _safe_execute(action, None)
    if not rows:
        return None

    values: List[float] = []
    game_details = []
    for row in rows:
        val = _stat_value_for_prop(row, prop)
        if val is not None:
            values.append(val)
            game_details.append({"game_date": row.get("game_date"), "value": val})

    if not values:
        return None

    over = sum(1 for v in values if v > line_value)
    under = sum(1 for v in values if v < line_value)
    last_game = game_details[0] if game_details else None

    return {
        "over": over,
        "under": under,
        "games": len(values),
        "line": line_value,
        "values": values,
        "last_game": last_game,
    }


def upsert_player_logs(sport: str, logs: List[Dict[str, Any]]) -> int:
    table = _stat_log_table(sport)
    if not table or not logs:
        return 0
    def action():
        res = supabase.table(table).upsert(logs).execute()
        return len(res.data or logs)
    return _safe_execute(action, 0)