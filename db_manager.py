import os
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency in some environments
    httpx = None

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


def _safe_execute(action, fallback):
    if not supabase:
        return fallback
    try:
        return action()
    except Exception as e:
        logger.error(f"Supabase execution error: {e}")
        return fallback


def validate_supabase_connection() -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": False, "connected": False, "errors": [], "tables": {}}
    if not supabase:
        result["errors"].append("Supabase client is not configured")
        return result

    try:
        probe = supabase.table("bets_log").select("id", count="exact").limit(1).execute()
        result["connected"] = probe is not None
    except Exception as e:
        result["errors"].append(f"Supabase connection failed: {e}")
        return result

    for table in REQUIRED_TABLES:
        try:
            probe = supabase.table(table).select("id", count="exact").limit(1).execute()
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
    if not supabase:
        return False

    bet_data = _normalize_bet_payload(*args, **kwargs)
    try:
        supabase.table("bets_log").insert(bet_data).execute()
        return True
    except Exception as first_error:
        logger.warning(f"bets_log insert failed, retrying legacy payload: {first_error}")
        legacy_payload = _legacy_bets_log_payload(bet_data)
        try:
            supabase.table("bets_log").insert(legacy_payload).execute()
            return True
        except Exception as second_error:
            logger.error(f"bets_log insert failed after legacy retry: {second_error}")
            return False


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
