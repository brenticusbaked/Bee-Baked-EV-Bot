import os
import json
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency in some environments
    httpx = None

logger = logging.getLogger("db_manager")

_RUNTIME_DB_STATS = {
    "bet_log_success": 0,
    "bet_log_failure": 0,
    "execution_log_success": 0,
    "execution_log_failure": 0,
}

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

_BET_ARG_NAMES = [
    "matchup",
    "market",
    "selection",
    "odds",
    "edge",
    "units",
    "fair_price",
    "sport",
    "event_id",
    "notes",
]

_LEGACY_BETS_LOG_FIELDS = {
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
    "created_at",
}


def _coerce_numeric(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_decimal_odds(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None

    parsed = _coerce_numeric(value)
    if parsed is None:
        return None

    text = str(value).strip()
    if text.startswith(("+", "-")):
        try:
            from utils.odds import american_to_decimal
            return float(american_to_decimal(text))
        except Exception:
            return None

    if isinstance(value, (int, float)) and abs(parsed) >= 10 and float(parsed).is_integer():
        try:
            from utils.odds import american_to_decimal
            return float(american_to_decimal(parsed))
        except Exception:
            pass

    return parsed if parsed > 1.0 else None


def _to_edge_pct(value: Any) -> Optional[float]:
    parsed = _coerce_numeric(value)
    if parsed is None:
        return None
    return parsed if abs(parsed) > 1.0 else parsed * 100.0


def _normalize_bet_payload(*args, **kwargs) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for name, value in zip(_BET_ARG_NAMES, args):
        payload[name] = value
    payload.update(kwargs)

    if "edge" not in payload and "edge_val" in payload:
        payload["edge"] = payload["edge_val"]

    if "date" not in payload or not payload.get("date"):
        payload["date"] = datetime.now(timezone.utc).date().isoformat()
    if "created_at" not in payload or not payload.get("created_at"):
        payload["created_at"] = datetime.now(timezone.utc).isoformat()

    if payload.get("odds_decimal") is None and payload.get("odds") is not None:
        payload["odds_decimal"] = _to_decimal_odds(payload.get("odds"))
    if payload.get("fair_price_decimal") is None and payload.get("fair_price") is not None:
        payload["fair_price_decimal"] = _to_decimal_odds(payload.get("fair_price"))
    if payload.get("edge_pct") is None and payload.get("edge") is not None:
        payload["edge_pct"] = _to_edge_pct(payload.get("edge"))
    if payload.get("market_type") is None and payload.get("market") is not None:
        payload["market_type"] = payload.get("market")

    return payload


def _legacy_bets_log_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if key in _LEGACY_BETS_LOG_FIELDS and value is not None}


def _parse_cache_timestamp(row: Dict[str, Any]) -> Optional[datetime]:
    for key in ("last_update", "captured_at", "updated_at"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _stat_candidates(prop: str) -> List[str]:
    key = str(prop or "").strip()
    base = key.lower()
    stripped = base
    for prefix in ("player_", "batter_", "pitcher_"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break

    aliases = {
        "batter_total_bases": ["total_bases"],
        "batter_hits": ["hits"],
        "batter_hits_runs_rbis": ["hits_runs_rbis"],
        "batter_rbi": ["rbis", "rbi"],
        "batter_rbis": ["rbis", "rbi"],
        "batter_home_runs": ["home_runs", "homeRuns"],
        "batter_home_run": ["home_runs", "homeRuns"],
        "batter_runs": ["runs"],
        "batter_runs_scored": ["runs"],
        "batter_walks": ["walks", "baseOnBalls"],
        "batter_stolen_bases": ["stolen_bases", "stolenBases"],
        "batter_strikeouts": ["strikeouts", "strikeOuts"],
        "pitcher_strikeouts": ["strikeouts", "strikeOuts"],
        "pitcher_outs": ["outs"],
        "pitcher_pitch_outs": ["outs"],
        "pitcher_hits_allowed": ["hits_allowed", "hits"],
        "pitcher_walks": ["walks_allowed", "walks", "baseOnBalls"],
        "pitcher_walks_allowed": ["walks_allowed", "walks", "baseOnBalls"],
        "player_points": ["points"],
        "player_rebounds": ["rebounds"],
        "player_assists": ["assists"],
        "player_threes": ["threes_made"],
        "player_threes_made": ["threes_made"],
        "player_three_pointers_made": ["threes_made"],
        "player_blocks": ["blocks"],
        "player_steals": ["steals"],
        "player_turnovers": ["turnovers"],
        "player_passing_yards": ["passing_yards"],
        "player_passing_yds": ["passing_yards"],
        "player_rushing_yards": ["rushing_yards"],
        "player_rushing_yds": ["rushing_yards"],
        "player_receiving_yards": ["receiving_yards"],
        "player_receiving_yds": ["receiving_yards"],
        "player_receptions": ["receptions"],
        "player_receiving_tds": ["receiving_tds"],
        "player_rushing_tds": ["rushing_tds"],
        "player_passing_tds": ["passing_tds"],
        "player_goals": ["goals"],
        "player_shots_on_goal": ["shots_on_goal"],
        "player_blocked_shots": ["blocked_shots"],
    }

    candidates = [base]
    if stripped and stripped not in candidates:
        candidates.append(stripped)
    if base in aliases:
        for alias in aliases[base]:
            if alias not in candidates:
                candidates.append(alias.lower())
    if stripped in aliases:
        for alias in aliases[stripped]:
            if alias not in candidates:
                candidates.append(alias.lower())
    return candidates


def _stat_lookup(row: Dict[str, Any], key: str) -> Optional[float]:
    for candidate in _stat_candidates(key):
        for source in (row, row.get("stats") if isinstance(row.get("stats"), dict) else {}):
            if not isinstance(source, dict):
                continue
            for source_key, value in source.items():
                if str(source_key).lower() == candidate and value is not None:
                    parsed = _coerce_numeric(value)
                    if parsed is not None:
                        return parsed
    return None


def _safe_execute(action, fallback, retries: int = 3, delay: float = 2.0):
    if not supabase:
        return fallback
    
    for attempt in range(retries):
        try:
            return action()
        except Exception as e:
            logger.warning(f"Supabase execution error (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                logger.error(f"Supabase execution failed permanently after {retries} attempts: {e}")
                return fallback
    return fallback


def _extract_cache_blob(rows: Any) -> Dict[str, Any]:
    def _group_event_list(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            sport_key = item.get("sport_key") or item.get("sport")
            if not sport_key:
                continue
            grouped.setdefault(str(sport_key), []).append(item)
        return grouped

    if isinstance(rows, dict):
        if isinstance(rows.get("data"), dict):
            return rows["data"]
        if isinstance(rows.get("payload"), dict):
            return rows["payload"]
        if isinstance(rows.get("data"), list):
            grouped = _group_event_list([item for item in rows["data"] if isinstance(item, dict)])
            return grouped or {"items": rows["data"]}
        if isinstance(rows.get("payload"), list):
            grouped = _group_event_list([item for item in rows["payload"] if isinstance(item, dict)])
            return grouped or {"items": rows["payload"]}
        return rows

    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            if isinstance(row.get("data"), dict):
                return row["data"]
            if isinstance(row.get("payload"), dict):
                return row["payload"]
            if isinstance(row.get("data"), list):
                grouped = _group_event_list([item for item in row["data"] if isinstance(item, dict)])
                return grouped or {"items": row["data"]}
            if isinstance(row.get("payload"), list):
                grouped = _group_event_list([item for item in row["payload"] if isinstance(item, dict)])
                return grouped or {"items": row["payload"]}
        return {}

    return {}


def validate_supabase_connection() -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "connected": False, "errors": [], "tables": {}}
    if not supabase:
        result["errors"].append("Supabase client is not configured")
        return result

    try:
        probe = supabase.table("bets_log").select("*", count="exact").limit(1).execute()
        result["connected"] = probe is not None
    except Exception as e:
        result["errors"].append(f"Supabase connection failed: {e}")
        return result

    for table in REQUIRED_TABLES:
        try:
            probe = supabase.table(table).select("*", count="exact").limit(1).execute()
            result["tables"][table] = probe is not None
        except Exception as e:
            result["tables"][table] = False
            result["errors"].append(f"Table '{table}' not accessible: {e}")

    result["ok"] = result["connected"] and not result["errors"]
    return result


def _force_postgrest_http1(client):
    try:
        if httpx is None:
            return
        if not hasattr(client, "postgrest") or not hasattr(client.postgrest, "session"):
            return

        session = client.postgrest.session
        if not isinstance(session, httpx.Client):
            return

        if hasattr(session, "_transport") and hasattr(session._transport, "_pool"):
            pool = session._transport._pool
            if hasattr(pool, "_http2") and pool._http2 is False:
                return

        new_session = httpx.Client(
            base_url=str(session.base_url),
            headers=dict(session.headers),
            timeout=getattr(session, "timeout", None),
            follow_redirects=getattr(session, "follow_redirects", False),
            http2=False,
        )
        client.postgrest.session = new_session
    except Exception:
        pass


def get_market_cache(max_age_minutes: Optional[int] = None) -> Dict[str, Any]:
    del max_age_minutes
    return get_master_cache()


def save_market_cache(cache_data: Dict[str, Any]):
    save_master_cache(cache_data)


def get_master_cache() -> Dict[str, Any]:
    def action():
        res = supabase.table("odds_cache").select("*").order("updated_at", desc=True).limit(1).execute()
        return _extract_cache_blob(res.data or {})
    return _safe_execute(action, {})


def save_master_cache(cache_data: Dict[str, Any]):
    def action():
        supabase.table("odds_cache").upsert({"id": "master", "data": cache_data, "updated_at": datetime.now(timezone.utc).isoformat()}).execute()
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


def get_ungraded_past_bets() -> List[Dict[str, Any]]:
    bets = get_all_bets()
    return [
        bet
        for bet in bets
        if str(bet.get("result") or "").strip() == ""
        and bet.get("graded_at") in (None, "")
    ]


def update_result(bet_id: Any, result: str) -> bool:
    if not supabase:
        return False

    def action():
        supabase.table("bets_log").update(
            {
                "result": result,
                "graded_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", bet_id).execute()
        return True

    return _safe_execute(action, False)


def log_bet_to_db(*args, **kwargs) -> bool:
    if not supabase:
        _RUNTIME_DB_STATS["bet_log_failure"] += 1
        return False

    bet_data = _normalize_bet_payload(*args, **kwargs)
    try:
        def action():
            supabase.table("bets_log").insert(bet_data).execute()
            return True
        if _safe_execute(action, False):
            _RUNTIME_DB_STATS["bet_log_success"] += 1
            return True
    except Exception as first_error:
        logger.warning(f"bets_log insert failed, retrying legacy payload: {first_error}")
        legacy_payload = _legacy_bets_log_payload(bet_data)
        try:
            def legacy_action():
                supabase.table("bets_log").insert(legacy_payload).execute()
                return True
            if _safe_execute(legacy_action, False):
                _RUNTIME_DB_STATS["bet_log_success"] += 1
                return True
        except Exception as second_error:
            logger.error(f"bets_log insert failed after legacy retry: {second_error}")

    _RUNTIME_DB_STATS["bet_log_failure"] += 1
    return False


def is_already_logged(*args) -> bool:
    if len(args) == 3:
        sport = None
        matchup, market, selection = args
    elif len(args) == 4:
        sport, event_id, market, selection = args
    else:
        raise TypeError("is_already_logged() expects 3 or 4 positional arguments")

    def action():
        query = supabase.table("bets_log").select("id")
        if sport is not None:
            query = query.eq("sport", sport)
        if len(args) == 3:
            query = query.eq("matchup", matchup)
        elif event_id is not None:
            query = query.eq("event_id", event_id)
        res = query.eq("market", market).eq("selection", selection).execute()
        return len(res.data or []) > 0
    return _safe_execute(action, False)


def update_bet_clv(bet_id: Any, closing_odds: Any, clv_pct: Any, closing_line: Any = None):
    def action():
        update_data = {
            "closing_line_american": closing_odds,
            "clv_edge_pct": clv_pct,
            "clv_tracked_at": datetime.now(timezone.utc).isoformat(),
        }
        if closing_line is not None:
            update_data["closing_line_decimal"] = closing_line
        supabase.table("bets_log").update(update_data).eq("id", bet_id).execute()
    _safe_execute(action, None)


def load_tracker_state(sport: str, fallback: Dict[str, Any] = None) -> Dict[str, Any]:
    def action():
        res = supabase.table("bot_state").select("*").eq("id", sport).limit(1).execute()
        if res.data and len(res.data) > 0:
            row = res.data[0]
            if isinstance(row.get("data"), dict):
                return row["data"]
            if isinstance(row.get("state"), dict):
                return row["state"]
        return fallback or {}
    return _safe_execute(action, fallback or {})


def save_tracker_state(sport: str, data: Dict[str, Any], fallback: Dict[str, Any] = None):
    def action():
        supabase.table("bot_state").upsert({"id": sport, "data": data, "updated_at": datetime.now(timezone.utc).isoformat()}).execute()
    _safe_execute(action, None)


def log_alert_event(*args, **kwargs):
    sport = kwargs.pop("sport", kwargs.pop("source", None))
    alert = kwargs.pop("alert", None)
    alert_type = kwargs.pop("alert_type", None)
    dedupe_key = kwargs.pop("dedupe_key", None)
    count = kwargs.pop("count", 1)
    payload_preview = kwargs.pop("payload_preview", None)
    status = kwargs.pop("status", "sent")

    if args:
        if len(args) >= 1 and sport is None:
            sport = args[0]
        if len(args) >= 2 and alert is None:
            alert = args[1]
        if len(args) >= 3 and dedupe_key is None:
            dedupe_key = args[2]
        if len(args) >= 4:
            count = args[3]
        if len(args) >= 5 and payload_preview is None:
            payload_preview = args[4]
        if len(args) >= 6:
            status = args[5]

    if alert is None:
        alert = alert_type or {}

    if isinstance(alert, dict):
        if alert_type is None:
            alert_type = alert.get("alert_type") or alert.get("type") or alert.get("event_type")
    else:
        alert = {"value": alert}

    if payload_preview is not None and not isinstance(payload_preview, str):
        try:
            payload_preview = json.dumps(payload_preview, default=str)
        except Exception:
            payload_preview = str(payload_preview)

    def action():
        payload = {
            "source": sport,
            "alert_type": alert_type,
            "dedupe_key": dedupe_key,
            "count": count,
            "payload_preview": payload_preview,
            "status": status,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("alerts_sent").insert(payload).execute()
    _safe_execute(action, None)


def alert_already_sent(alert_type: str, dedupe_key: str, window_hours: int = 24) -> bool:
    def action():
        res = (supabase.table("alerts_sent")
               .select("id")
               .eq("alert_type", alert_type)
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
        _RUNTIME_DB_STATS["execution_log_success"] += 1
        return True
    result = _safe_execute(action, False)
    if not result:
        _RUNTIME_DB_STATS["execution_log_failure"] += 1
    return result


def reset_runtime_db_stats() -> None:
    for key in _RUNTIME_DB_STATS:
        _RUNTIME_DB_STATS[key] = 0


def get_runtime_db_stats() -> Dict[str, int]:
    return dict(_RUNTIME_DB_STATS)


def log_workflow_run(
    workflow_name: str,
    status: str,
    runtime_seconds: float,
    task_count: int,
    failure_count: int,
    alert_count: int,
    graded_count: int,
    tracked_count: int,
    summary: str,
) -> bool:
    if not supabase:
        return False

    def action():
        payload = {
            "workflow_name": workflow_name,
            "status": status,
            "runtime_seconds": runtime_seconds,
            "task_count": task_count,
            "failure_count": failure_count,
            "alert_count": alert_count,
            "graded_count": graded_count,
            "tracked_count": tracked_count,
            "summary": summary,
            "run_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("workflow_runs").insert(payload).execute()
        return True

    return _safe_execute(action, False)


def _execution_payload_from_report(report: Dict[str, Any]) -> Dict[str, Any]:
    def _json_safe(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: _json_safe(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [_json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [_json_safe(item) for item in value]
        return value

    def _enum_value(value: Any) -> Any:
        return value.value if hasattr(value, "value") else value

    def _datetime_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    payload = dict(report)
    parent_order = payload.get("parent_order") or payload.get("order") or {}
    if isinstance(parent_order, dict):
        order = dict(parent_order)
        order.setdefault("order_id", parent_order.get("order_id"))
        order["side"] = _enum_value(order.get("side"))
        order["order_type"] = _enum_value(order.get("order_type"))
        order["time_in_force"] = _enum_value(order.get("time_in_force"))
        order["created_at"] = _datetime_value(order.get("created_at"))
        for key in ("filled_quantity", "fill_rate", "average_price", "slippage", "edge_capture", "fees"):
            if key not in order and isinstance(payload.get("metrics"), dict):
                order[key] = payload["metrics"].get(key)
        if isinstance(payload.get("metrics"), dict):
            order.update({key: payload["metrics"].get(key) for key in ("filled_quantity", "fill_rate", "average_price", "slippage", "edge_capture", "fees")})
        payload["order"] = order
        payload["parent_order"] = order

    child_orders = []
    for child in payload.get("child_orders", []) or []:
        if not isinstance(child, dict):
            continue
        normalized_child = dict(child)
        normalized_child["side"] = _enum_value(normalized_child.get("side"))
        normalized_child["status"] = _enum_value(normalized_child.get("status"))
        child_orders.append(normalized_child)
    payload["child_orders"] = child_orders

    fills = []
    fills_by_child: Dict[str, List[Dict[str, Any]]] = {}
    for index, fill in enumerate(payload.get("fills", []) or [], start=1):
        if not isinstance(fill, dict):
            continue
        normalized_fill = dict(fill)
        normalized_fill["side"] = _enum_value(normalized_fill.get("side"))
        normalized_fill["filled_at"] = _datetime_value(normalized_fill.get("filled_at"))
        child_order_id = normalized_fill.get("child_order_id")
        if child_order_id:
            normalized_fill.setdefault("fill_id", f"{child_order_id}-{index}")
            fills_by_child.setdefault(child_order_id, []).append(normalized_fill)
        fills.append(normalized_fill)
    payload["fills"] = fills

    venue_metrics = []
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    for index, child in enumerate(child_orders, start=1):
        child_order_id = child.get("child_order_id")
        child_fills = fills_by_child.get(child_order_id, [])
        routed_quantity = _coerce_numeric(child.get("quantity")) or 0.0
        filled_quantity = sum(_coerce_numeric(fill.get("quantity")) or 0.0 for fill in child_fills)
        notional = sum(((_coerce_numeric(fill.get("quantity")) or 0.0) * (_coerce_numeric(fill.get("price")) or 0.0)) for fill in child_fills)
        fee_total = sum(_coerce_numeric(fill.get("fee")) or 0.0 for fill in child_fills)
        average_fill_price = (notional / filled_quantity) if filled_quantity else None
        route_score = _coerce_numeric(child.get("route_score"))
        metadata = child.get("metadata") if isinstance(child.get("metadata"), dict) else {}
        venue_metric = {
            "metric_id": f"{child_order_id}-M{index}" if child_order_id else None,
            "parent_order_id": child.get("parent_order_id") or (payload.get("order") or {}).get("order_id"),
            "child_order_id": child_order_id,
            "venue_id": child.get("venue_id"),
            "symbol": child.get("symbol"),
            "status": child.get("status"),
            "routed_quantity": round(routed_quantity, 6),
            "filled_quantity": round(filled_quantity, 6),
            "fill_rate": round(filled_quantity / routed_quantity, 6) if routed_quantity else 0.0,
            "average_fill_price": round(average_fill_price, 6) if average_fill_price is not None else None,
            "route_score": route_score,
            "fee": round(fee_total, 6),
            "latency_ms": metadata.get("latency_ms"),
            "fill_probability": metadata.get("fill_probability"),
            "edge_capture": metrics.get("edge_capture"),
            "measured_at": child_fills[0].get("filled_at") if child_fills else datetime.now(timezone.utc).isoformat(),
        }
        venue_metrics.append(venue_metric)
    payload["venue_metrics"] = venue_metrics

    return _json_safe(payload)


def get_latest_rows(table: str, column: str, limit: int = 5) -> List[Dict[str, Any]]:
    def action():
        res = supabase.table(table).select("*").order(column, desc=True).limit(limit).execute()
        return res.data or []
    return _safe_execute(action, [])


def get_table_count(table: str) -> int:
    def action():
        res = supabase.table(table).select("*", count="exact").execute()
        return res.count or 0
    return _safe_execute(action, 0)


def assemble_cache(fetch_func=None, odds_data=None) -> Dict[str, Any]:
    fixtures_source = fetch_func() if callable(fetch_func) else fetch_func
    fixtures: List[Dict[str, Any]] = []
    if isinstance(fixtures_source, list):
        fixtures = [dict(item) for item in fixtures_source if isinstance(item, dict)]
    elif isinstance(fixtures_source, dict):
        fixtures = [dict(fixtures_source)]

    if not odds_data:
        if fixtures:
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for fixture in fixtures:
                sport = fixture.get("sport_key")
                if not sport:
                    continue
                event = dict(fixture)
                event.setdefault("bookmakers", [])
                grouped.setdefault(sport, []).append(event)
            return grouped
        return fixtures_source or {}

    if not isinstance(odds_data, list):
        return odds_data

    fixture_index = {
        (fixture.get("sport_key"), fixture.get("id")): fixture
        for fixture in fixtures
        if fixture.get("sport_key") and fixture.get("id") is not None
    }

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    event_index: Dict[tuple, Dict[str, Any]] = {}

    def ensure_event(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sport = row.get("sport_key")
        fixture_id = row.get("fixture_id")
        if not sport:
            return None
        key = (sport, fixture_id)
        event = event_index.get(key)
        if event is None:
            event = dict(fixture_index.get(key, {}))
            event.setdefault("id", fixture_id)
            event.setdefault("sport_key", sport)
            event.setdefault("bookmakers", [])
            grouped.setdefault(sport, []).append(event)
            event_index[key] = event
        else:
            event.setdefault("bookmakers", [])
            if key in fixture_index:
                for field, value in fixture_index[key].items():
                    if field != "bookmakers" and value is not None and event.get(field) is None:
                        event[field] = value
        return event

    def ensure_bookmaker(event: Dict[str, Any], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        book_key = row.get("bookmaker_key")
        if not book_key:
            return None
        books = event.setdefault("bookmakers", [])
        book = next((item for item in books if item.get("key") == book_key), None)
        if book is None:
            book = {
                "key": book_key,
                "title": row.get("bookmaker_title"),
                "markets": [],
            }
            books.append(book)
        return book

    def ensure_market(bookmaker: Dict[str, Any], row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        market_key = row.get("market_key")
        if not market_key:
            return None
        markets = bookmaker.setdefault("markets", [])
        market = next((item for item in markets if item.get("key") == market_key), None)
        if market is None:
            market = {"key": market_key, "outcomes": []}
            markets.append(market)
        return market

    def outcome_key(row: Dict[str, Any]) -> tuple:
        if not isinstance(row, dict):
            return (None, None, None)
        return (
            row.get("outcome_name", row.get("name")),
            row.get("outcome_description", row.get("description")),
            row.get("point"),
        )

    for row in odds_data:
        if not isinstance(row, dict):
            continue
        event = ensure_event(row)
        if event is None:
            continue
        bookmaker = ensure_bookmaker(event, row)
        if bookmaker is None:
            continue
        market = ensure_market(bookmaker, row)
        if market is None:
            continue

        current = {
            "name": row.get("outcome_name"),
            "description": row.get("outcome_description"),
            "point": row.get("point"),
            "price": row.get("price_decimal"),
            "last_update": row.get("last_update"),
        }
        stamp = _parse_cache_timestamp(row)
        current["_stamp"] = stamp

        outcomes = market.setdefault("outcomes", [])
        existing = next((item for item in outcomes if outcome_key(item) == outcome_key(row)), None)
        if existing is None:
            outcomes.append(current)
            continue

        existing_stamp = existing.get("_stamp")
        if existing_stamp is None or (stamp is not None and stamp >= existing_stamp):
            existing.update(current)

    for events in grouped.values():
        for event in events:
            event.pop("_stamp", None)
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        outcome.pop("_stamp", None)

    return grouped


def hydrate_market_cache() -> Dict[str, Any]:
    """Rebuild and persist the odds cache from Supabase fixtures + historical odds."""
    if not supabase:
        return {"detail": "Supabase client is not configured", "count": 0, "label": "updates", "meta": {}}

    def action():
        fixtures_res = supabase.table("fixtures").select("*").execute()
        odds_res = supabase.table("historical_odds").select("*").execute()
        fixtures = fixtures_res.data or []
        historical_odds = odds_res.data or []
        cache = assemble_cache(fixtures, historical_odds)
        save_master_cache(cache)
        sport_count = len(cache) if isinstance(cache, dict) else 0
        event_count = sum(len(events) for events in cache.values()) if isinstance(cache, dict) else 0
        return {
            "detail": "market cache hydrated from Supabase",
            "count": event_count,
            "label": "updates",
            "meta": {
                "cache_sports": sport_count,
                "cache_events": event_count,
            },
        }

    return _safe_execute(action, {"detail": "market cache hydration failed", "count": 0, "label": "updates", "meta": {}})


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
    value = _stat_lookup(row, prop)
    return value


def get_l10_hit_rate(
    player: str,
    prop: str,
    line: float,
    sport: str,
    games: int = 10,
    opponent: str | None = None,
) -> Optional[Dict[str, Any]]:
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
            .limit(max(1, int(games) * (5 if opponent else 1)))
            .execute()
            .data
        )

    rows = _safe_execute(action, None)
    if not rows:
        return None

    opponent_key = str(opponent or "").strip().lower()
    values: List[float] = []
    game_details = []
    last_vs_game = None
    for row in rows:
        val = _stat_value_for_prop(row, prop)
        if val is not None:
            values.append(val)
            detail = {
                "game_date": row.get("game_date"),
                "value": val,
                "opponent": row.get("opponent"),
            }
            game_details.append(detail)
            if opponent_key:
                row_opponent = str(row.get("opponent") or "").strip().lower()
                if row_opponent and row_opponent == opponent_key and last_vs_game is None:
                    last_vs_game = detail

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
        "last_vs_game": last_vs_game,
        "opponent": opponent,
    }


def upsert_player_logs(sport: str, logs: List[Dict[str, Any]]) -> int:
    table = _stat_log_table(sport)
    if not table or not logs:
        return 0
    def action():
        res = supabase.table(table).upsert(logs).execute()
        return len(res.data or logs)
    return _safe_execute(action, 0)