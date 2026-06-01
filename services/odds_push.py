import asyncio
import copy
import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Dict, Iterable, List, Optional, Tuple

from db_manager import get_master_cache, save_master_cache
from services.live_edges import find_live_edge_alerts, send_live_edge_alerts


Cache = Dict[str, List[dict]]


def _outcome_key(outcome: dict) -> Tuple[str, str]:
    return (str(outcome.get("name", "")).strip(), str(outcome.get("point", "")).strip())


def _merge_outcomes(existing_market: dict, incoming_market: dict) -> None:
    existing_outcomes = existing_market.setdefault("outcomes", [])
    outcome_index = {_outcome_key(outcome): outcome for outcome in existing_outcomes}
    for outcome in incoming_market.get("outcomes", []):
        outcome_index[_outcome_key(outcome)] = outcome
    existing_market["outcomes"] = list(outcome_index.values())


def _merge_bookmakers(existing_event: dict, incoming_event: dict) -> None:
    existing_books = existing_event.setdefault("bookmakers", [])
    book_index = {book.get("key"): book for book in existing_books}

    for incoming_book in incoming_event.get("bookmakers", []):
        book_key = incoming_book.get("key")
        if book_key not in book_index:
            existing_books.append(incoming_book)
            book_index[book_key] = incoming_book
            continue

        current_book = book_index[book_key]
        current_markets = current_book.setdefault("markets", [])
        market_index = {market.get("key"): market for market in current_markets}
        for incoming_market in incoming_book.get("markets", []):
            market_key = incoming_market.get("key")
            if market_key not in market_index:
                current_markets.append(incoming_market)
                market_index[market_key] = incoming_market
                continue
            _merge_outcomes(market_index[market_key], incoming_market)


def merge_cache_events(cache: Cache, sport: str, events: Iterable[dict]) -> int:
    sport_key = str(sport or "unknown").strip()
    cache.setdefault(sport_key, [])
    event_index = {str(event.get("id")): event for event in cache[sport_key]}
    merged = 0

    for event in events:
        event_id = str(event.get("id") or event.get("event_id") or event.get("game_id") or "")
        if not event_id:
            continue
        event["id"] = event_id
        if event_id not in event_index:
            cache[sport_key].append(event)
            event_index[event_id] = event
        else:
            _merge_bookmakers(event_index[event_id], event)
        merged += 1
    return merged


def _coerce_message(message) -> dict | list:
    if isinstance(message, (dict, list)):
        return message
    if isinstance(message, bytes):
        message = message.decode("utf-8")
    return json.loads(message)


def _candidate_payloads(payload) -> List[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("events"), list):
        return [item for item in payload["events"] if isinstance(item, dict)]
    for key in ("event", "data", "payload", "message"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            return _candidate_payloads(nested) or [nested]
    return [payload]


def extract_cache_events(message, default_sport: Optional[str] = None) -> Cache:
    payload = _coerce_message(message)
    sport_hint = default_sport
    if isinstance(payload, dict):
        sport_hint = payload.get("sport_key") or payload.get("sport") or sport_hint

    cache_events: Cache = {}
    for event in _candidate_payloads(payload):
        if "bookmakers" not in event:
            continue
        sport = event.get("sport_key") or event.get("sport") or sport_hint
        if not sport:
            continue
        cache_events.setdefault(str(sport), []).append(event)
    return cache_events


def merge_push_message(cache: Cache, message, default_sport: Optional[str] = None) -> int:
    updates = extract_cache_events(message, default_sport=default_sport)
    return sum(merge_cache_events(cache, sport, events) for sport, events in updates.items())


def build_connection_config(url: str, api_key: Optional[str] = None) -> tuple[str, dict]:
    headers = {}
    if not api_key:
        return url, headers

    auth_mode = os.getenv("ODDS_PUSH_AUTH_MODE", "header").strip().lower()
    if auth_mode == "query":
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.setdefault(os.getenv("ODDS_PUSH_API_KEY_PARAM", "apiKey"), api_key)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)), headers

    auth_header = os.getenv("ODDS_PUSH_AUTH_HEADER", "X-API-Key")
    headers[auth_header] = f"Bearer {api_key}" if auth_header.lower() == "authorization" else api_key
    return url, headers


async def stream_push_feed(
    url: str,
    api_key: Optional[str] = None,
    subscribe_payload: Optional[dict] = None,
    default_sport: Optional[str] = None,
    max_messages: Optional[int] = None,
) -> int:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("Install the optional 'websockets' dependency before running the push feed.") from exc

    url, headers = build_connection_config(url, api_key=api_key)

    processed = 0
    sent_dedupe_keys: set[str] = set()
    cache = get_master_cache() or {}
    connect_kwargs = {}
    if headers:
        connect_kwargs["additional_headers"] = headers
    try:
        websocket_context = websockets.connect(url, **connect_kwargs)
    except TypeError:
        websocket_context = websockets.connect(url, extra_headers=headers or None)

    async with websocket_context as websocket:
        if subscribe_payload:
            await websocket.send(json.dumps(subscribe_payload))

        while max_messages is None or processed < max_messages:
            message = await websocket.recv()
            previous_cache = copy.deepcopy(cache)
            updates = extract_cache_events(message, default_sport=default_sport)
            merged = sum(merge_cache_events(cache, sport, events) for sport, events in updates.items())
            if merged:
                save_master_cache(cache)
                alerts = find_live_edge_alerts(previous_cache, cache, updates)
                sent = send_live_edge_alerts(alerts, sent_dedupe_keys=sent_dedupe_keys)
                print(f"odds_push merged {merged} event update(s)")
                if alerts:
                    print(f"odds_push live edge alerts: {sent}/{len(alerts)} sent")
            processed += 1
    return processed


def run_push_feed() -> dict:
    url = os.getenv("ODDS_PUSH_WS_URL")
    if not url:
        return {"detail": "ODDS_PUSH_WS_URL missing", "count": 0, "label": "updates"}

    subscribe_payload = None
    subscribe_json = os.getenv("ODDS_PUSH_SUBSCRIBE_JSON")
    if subscribe_json:
        subscribe_payload = json.loads(subscribe_json)

    max_messages = os.getenv("ODDS_PUSH_MAX_MESSAGES")
    count = asyncio.run(
        stream_push_feed(
            url=url,
            api_key=os.getenv("ODDS_PUSH_API_KEY"),
            subscribe_payload=subscribe_payload,
            default_sport=os.getenv("ODDS_PUSH_SPORT"),
            max_messages=int(max_messages) if max_messages else None,
        )
    )
    provider = os.getenv("ODDS_PUSH_PROVIDER", "odds_push")
    return {"detail": f"{provider} stream processed", "count": count, "label": "updates"}
