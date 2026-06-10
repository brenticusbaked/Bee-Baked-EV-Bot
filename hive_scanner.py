"""
BEE BAKED BETS  -  The Hive +EV Scanner
========================================
Standalone Pinnacle-vs-US-books Expected Value scanner.

Architecture:
  1. Pull EU-region odds to isolate Pinnacle (sharp baseline).
  2. Pull US-region odds for recreational sportsbooks.
  3. De-vig Pinnacle lines to derive true probabilities.
  4. Compare recreational prices against the true line.
  5. Flag +EV bets, size with Quarter-Kelly, alert Discord.

Optimizations:
  - Dynamic time-to-match polling: adjusts scan frequency based on how
    close each sport's next game is.  Saves API credits by not polling
    stale lines far from game time.
  - Market expansion: pulls h2h, spreads, totals in a single unified call.
  - API response caching: skips redundant API calls when fresh data exists.
  - seen_bets.json dedup: prevents Discord spam on identical alerts.

Run once:   python hive_scanner.py
Loop mode:  python hive_scanner.py --loop
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Minimum EV% to fire an alert (as a decimal, e.g. 0.02 = 2%).
EV_THRESHOLD = float(os.getenv("HIVE_EV_THRESHOLD", "0.02"))

# Sports to scan - The Odds API sport keys.
SPORTS = [
    s.strip()
    for s in os.getenv(
        "HIVE_SPORTS",
        "basketball_nba,baseball_mlb,icehockey_nhl,americanfootball_nfl,basketball_wnba",
    ).split(",")
    if s.strip()
]

# Markets to scan per sport — batched in a single API call.
MARKETS = os.getenv("HIVE_MARKETS", "h2h,spreads,totals")

# US recreational sportsbooks we compare against Pinnacle.
# Expanded to maximise surface area for slow-moving lines.
SOFT_BOOKS = {
    b.strip()
    for b in os.getenv(
        "HIVE_SOFT_BOOKS",
        "draftkings,fanduel,betmgm,caesars,bet365,betrivers",
    ).split(",")
    if b.strip()
}

# De-vig method: "multiplicative" or "power" (default).
DEVIG_METHOD = os.getenv("HIVE_DEVIG_METHOD", "power")

# Quarter-Kelly cap (max bankroll % per bet).
KELLY_CAP = float(os.getenv("HIVE_KELLY_CAP", "5.0"))

# Embed colour (green).
EMBED_COLOR = 0x2ECC71

# ---------------------------------------------------------------------------
# Cache / Dedup paths
# ---------------------------------------------------------------------------

# API response cache: stores raw odds JSON with timestamps.
API_CACHE_PATH = os.getenv("HIVE_API_CACHE", "hive_api_cache.json")

# Seen-bets dedup: hashes of (game+market+book+odds) with timestamps.
SEEN_BETS_PATH = os.getenv("HIVE_SEEN_BETS", "seen_bets.json")

# How many minutes before a seen-bet hash can be re-alerted.
SEEN_BETS_TTL_MINUTES = float(os.getenv("HIVE_SEEN_BETS_TTL_MINUTES", "60"))

# Game schedule cache: stores commence_time per event.
SCHEDULE_CACHE_PATH = os.getenv("HIVE_SCHEDULE_CACHE", "hive_schedule_cache.json")

# How often (in hours) to refresh the full game schedule.
SCHEDULE_REFRESH_HOURS = float(os.getenv("HIVE_SCHEDULE_REFRESH_HOURS", "12"))

# ---------------------------------------------------------------------------
# Mathematical Primitives
# ---------------------------------------------------------------------------


def american_to_decimal(american_odds: float) -> float:
    """Convert American odds to decimal odds."""
    if american_odds > 0:
        return (american_odds / 100.0) + 1.0
    if american_odds < 0:
        return (100.0 / abs(american_odds)) + 1.0
    raise ValueError("American odds cannot be zero")


def decimal_to_american(decimal_odds: float) -> str:
    """Convert decimal odds to American odds string."""
    if decimal_odds >= 2.0:
        return f"+{int(round((decimal_odds - 1) * 100))}"
    return str(int(round(-100 / (decimal_odds - 1))))


def implied_probability(decimal_odds: float) -> float:
    """Decimal odds -> raw implied probability."""
    if decimal_odds <= 1.0:
        return 0.0
    return 1.0 / decimal_odds


def multiplicative_devig(probs: List[float]) -> List[float]:
    """Remove vig proportionally (normalize to sum=1)."""
    total = sum(probs)
    if total <= 0:
        return [0.0] * len(probs)
    return [p / total for p in probs]


def power_devig(probs: List[float], tol: float = 1e-12, max_iter: int = 100) -> List[float]:
    """Remove vig via power method (raises each prob to an exponent so sum=1).

    Falls back to multiplicative if any input is non-positive.
    """
    if len(probs) < 2:
        return [1.0] if probs else []
    if any(p <= 0 for p in probs):
        return multiplicative_devig(probs)

    total = sum(probs)
    if abs(total - 1.0) <= tol:
        return list(probs)

    lo, hi = 0.01, 10.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        s = sum(p ** mid for p in probs)
        if abs(s - 1.0) <= tol:
            break
        if s > 1.0:
            lo = mid
        else:
            hi = mid

    exp = (lo + hi) / 2.0
    fair = [p ** exp for p in probs]
    fair_total = sum(fair)
    if fair_total <= 0:
        return multiplicative_devig(probs)
    return [p / fair_total for p in fair]


def devig(probs: List[float], method: str = "power") -> List[float]:
    """Dispatch to the chosen de-vig method."""
    if method in ("multiplicative", "mult", "proportional"):
        return multiplicative_devig(probs)
    return power_devig(probs)


def calculate_ev(true_prob: float, decimal_odds: float) -> float:
    """EV% = (TrueProb * Payout) - (ProbLoss * Wager).

    Returns the edge as a fraction (0.05 = 5% EV).
    With unit wager of 1:
      EV = true_prob * (decimal_odds - 1) - (1 - true_prob) * 1
         = true_prob * decimal_odds - 1
    """
    return true_prob * decimal_odds - 1.0


def quarter_kelly(true_prob: float, decimal_odds: float) -> float:
    """Quarter-Kelly bankroll fraction.

    Full Kelly: f* = (b*p - q) / b
      where b = decimal_odds - 1, p = true_prob, q = 1 - p.
    Quarter-Kelly = f* / 4.
    Returns as a percentage of bankroll (e.g. 1.25 means 1.25%).
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    p = true_prob
    q = 1.0 - p
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0.0
    qk = full_kelly / 4.0
    return min(qk * 100.0, KELLY_CAP)


# ---------------------------------------------------------------------------
# JSON Helpers
# ---------------------------------------------------------------------------


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path: str, data: dict) -> None:
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except OSError as exc:
        print(f"[hive] JSON save error ({path}): {exc}")


# ---------------------------------------------------------------------------
# Strategy 1: Dynamic Time-to-Match Polling
# ---------------------------------------------------------------------------


def polling_interval_seconds(hours_to_match: float) -> Optional[int]:
    """Return the recommended polling interval (seconds) based on time to match.

    Returns None if the sport should NOT be polled (> 24h out or post-game).
    """
    if hours_to_match < 0:
        return None  # Post-game
    if hours_to_match > 24:
        return None  # Too far out
    if hours_to_match > 12:
        return 3600  # 60 min
    if hours_to_match > 2:
        return 1800  # 30 min
    return 300  # 5 min


def _fetch_schedule(sport: str) -> List[dict]:
    """Fetch upcoming events for a sport (no odds, just schedule). Free call."""
    if not ODDS_API_KEY:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/events"
    params = {"apiKey": ODDS_API_KEY}
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        print(f"[hive] Schedule fetch error for {sport}: {exc}")
        return []


def refresh_schedule_cache() -> dict:
    """Refresh game schedule from API and cache it.

    Returns {sport: [{id, commence_time, home_team, away_team}, ...]}
    """
    cache = _load_json(SCHEDULE_CACHE_PATH)
    last_refresh = cache.get("_last_refresh", 0)
    now = time.time()

    if now - last_refresh < SCHEDULE_REFRESH_HOURS * 3600:
        print(f"[hive] Schedule cache is fresh ({(now - last_refresh) / 3600:.1f}h old). Skipping refresh.")
        return cache

    print("[hive] Refreshing game schedule ...")
    for sport in SPORTS:
        events = _fetch_schedule(sport)
        cache[sport] = [
            {
                "id": ev.get("id"),
                "commence_time": ev.get("commence_time", ""),
                "home_team": ev.get("home_team", ""),
                "away_team": ev.get("away_team", ""),
            }
            for ev in events
        ]
        print(f"[hive] {sport}: {len(events)} upcoming events cached")

    cache["_last_refresh"] = now
    _save_json(SCHEDULE_CACHE_PATH, cache)
    return cache


def hours_until_next_game(schedule: dict, sport: str) -> float:
    """Return hours until the soonest upcoming game for the sport.

    Returns float('inf') if no games found.
    """
    events = schedule.get(sport, [])
    now = datetime.now(timezone.utc)
    min_hours = float("inf")
    for ev in events:
        ct = ev.get("commence_time", "")
        if not ct:
            continue
        try:
            start = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            delta_hours = (start - now).total_seconds() / 3600
            if delta_hours > -2:  # Include games that started < 2h ago (live)
                min_hours = min(min_hours, delta_hours)
        except (ValueError, TypeError):
            continue
    return min_hours


def should_poll_sport(schedule: dict, sport: str) -> Tuple[bool, Optional[int], float]:
    """Check if a sport should be polled now.

    Returns (should_poll, interval_seconds, hours_to_next_game).
    """
    hours = hours_until_next_game(schedule, sport)
    interval = polling_interval_seconds(hours)
    return interval is not None, interval, hours


# ---------------------------------------------------------------------------
# Strategy 3: API Response Cache
# ---------------------------------------------------------------------------


def _api_cache_key(sport: str, region: str) -> str:
    return f"{sport}|{region}"


def _get_cached_odds(sport: str, region: str, max_age_seconds: int) -> Optional[List[dict]]:
    """Return cached API response if it's fresh enough, else None."""
    cache = _load_json(API_CACHE_PATH)
    key = _api_cache_key(sport, region)
    entry = cache.get(key)
    if not entry:
        return None
    age = time.time() - entry.get("ts", 0)
    if age > max_age_seconds:
        return None
    print(f"[hive] Cache hit for {sport}/{region} ({age:.0f}s old)")
    return entry.get("data", [])


def _store_cached_odds(sport: str, region: str, data: List[dict]) -> None:
    """Store API response in the local cache."""
    cache = _load_json(API_CACHE_PATH)
    key = _api_cache_key(sport, region)
    cache[key] = {"ts": time.time(), "data": data}
    _save_json(API_CACHE_PATH, cache)


# ---------------------------------------------------------------------------
# Data Ingestion
# ---------------------------------------------------------------------------


def _fetch_odds(
    sport: str,
    region: str,
    bookmakers: Optional[str] = None,
    cache_max_age: int = 0,
) -> List[dict]:
    """Pull odds from The Odds API for a single sport + region.

    If cache_max_age > 0, returns cached data when available and fresh.
    """
    if cache_max_age > 0:
        cached = _get_cached_odds(sport, region, cache_max_age)
        if cached is not None:
            return cached

    if not ODDS_API_KEY:
        print("[hive] ODDS_API_KEY not set - skipping fetch")
        return []

    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": region,
        "markets": MARKETS,
        "oddsFormat": "decimal",
    }
    if bookmakers:
        params["bookmakers"] = bookmakers
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"[hive] {sport} ({region}): {len(data)} events  |  API credits left: {remaining}")
        _store_cached_odds(sport, region, data)
        return data
    except requests.RequestException as exc:
        print(f"[hive] API error for {sport}/{region}: {exc}")
        return []


def fetch_sharp_and_soft(
    sport: str, cache_max_age: int = 0
) -> Tuple[List[dict], List[dict]]:
    """Two API calls per sport: EU (Pinnacle) and US (soft books).

    cache_max_age: if > 0, use cached responses when available.
    """
    sharp_events = _fetch_odds(sport, "eu", bookmakers="pinnacle", cache_max_age=cache_max_age)
    soft_events = _fetch_odds(
        sport, "us", bookmakers=",".join(sorted(SOFT_BOOKS)), cache_max_age=cache_max_age
    )
    return sharp_events, soft_events


# ---------------------------------------------------------------------------
# Core Scanning Logic
# ---------------------------------------------------------------------------

OutcomeKey = Tuple[str, str]  # (name_lower, point_str)


def _extract_pinnacle_lines(events: List[dict]) -> Dict[str, Dict[str, Dict[OutcomeKey, float]]]:
    """Build {event_id: {market_key: {(name, point): decimal_price}}}."""
    result: Dict[str, Dict[str, Dict[OutcomeKey, float]]] = {}
    for event in events:
        eid = str(event.get("id", ""))
        for bm in event.get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            for mkt in bm.get("markets", []):
                mk = mkt["key"]
                for oc in mkt.get("outcomes", []):
                    key: OutcomeKey = (
                        str(oc["name"]).lower().strip(),
                        str(oc.get("point", "")),
                    )
                    result.setdefault(eid, {}).setdefault(mk, {})[key] = float(oc["price"])
    return result


def _devig_market(prices: Dict[OutcomeKey, float]) -> Dict[OutcomeKey, float]:
    """De-vig a two-way (or multi-way) market, returning true probabilities."""
    keys = list(prices.keys())
    raw_probs = [implied_probability(prices[k]) for k in keys]
    fair = devig(raw_probs, method=DEVIG_METHOD)
    return dict(zip(keys, fair))


def _scan_sport(sport: str, cache_max_age: int = 0) -> List[dict]:
    """Scan one sport: fetch data, de-vig Pinnacle, compare soft books."""
    sharp_events, soft_events = fetch_sharp_and_soft(sport, cache_max_age=cache_max_age)
    if not sharp_events or not soft_events:
        return []

    pinnacle = _extract_pinnacle_lines(sharp_events)

    event_meta = {}
    for ev in sharp_events:
        event_meta[str(ev.get("id", ""))] = {
            "home_team": ev.get("home_team", ""),
            "away_team": ev.get("away_team", ""),
            "commence_time": ev.get("commence_time", ""),
        }

    alerts = []
    for ev in soft_events:
        eid = str(ev.get("id", ""))
        if eid not in pinnacle:
            continue

        meta = event_meta.get(eid) or {
            "home_team": ev.get("home_team", ""),
            "away_team": ev.get("away_team", ""),
            "commence_time": ev.get("commence_time", ""),
        }
        matchup = f"{meta['away_team']} @ {meta['home_team']}"

        for bm in ev.get("bookmakers", []):
            book_key = bm.get("key", "")
            book_title = bm.get("title", book_key)
            if book_key not in SOFT_BOOKS:
                continue

            for mkt in bm.get("markets", []):
                mk = mkt["key"]
                sharp_prices = pinnacle.get(eid, {}).get(mk)
                if not sharp_prices or len(sharp_prices) < 2:
                    continue

                true_probs = _devig_market(sharp_prices)

                for oc in mkt.get("outcomes", []):
                    outcome_key: OutcomeKey = (
                        str(oc["name"]).lower().strip(),
                        str(oc.get("point", "")),
                    )
                    true_prob = true_probs.get(outcome_key)
                    if not true_prob or true_prob <= 0:
                        continue

                    offered_dec = float(oc["price"])
                    ev_pct = calculate_ev(true_prob, offered_dec)

                    if ev_pct > EV_THRESHOLD:
                        qk = quarter_kelly(true_prob, offered_dec)
                        selection = f"{oc['name']} {oc.get('point', '')}".strip()
                        fair_decimal = 1.0 / true_prob
                        sharp_price = sharp_prices.get(outcome_key)

                        alerts.append({
                            "sport": sport,
                            "matchup": matchup,
                            "market": mk,
                            "selection": selection,
                            "book": book_title,
                            "book_key": book_key,
                            "offered_odds_dec": offered_dec,
                            "offered_odds_am": decimal_to_american(offered_dec),
                            "sharp_odds_am": decimal_to_american(sharp_price) if sharp_price else "N/A",
                            "true_prob": true_prob,
                            "fair_value_am": decimal_to_american(fair_decimal),
                            "ev_pct": ev_pct,
                            "quarter_kelly_pct": qk,
                            "commence_time": meta.get("commence_time", ""),
                        })
    return alerts


def scan_all_sports(schedule: Optional[dict] = None) -> List[dict]:
    """Run the +EV scan across all configured sports.

    When a schedule is provided, respects dynamic polling windows:
    skips sports with no upcoming games and uses cached data when
    the polling interval hasn't elapsed yet.
    """
    all_alerts = []
    for sport in SPORTS:
        cache_max_age = 0  # default: always fetch fresh

        if schedule:
            should_poll, interval, hours = should_poll_sport(schedule, sport)
            if not should_poll:
                print(f"\n[hive] Skipping {sport} (next game in {hours:.1f}h — outside polling window)")
                continue
            # Use cached data if it's newer than the polling interval
            cache_max_age = interval or 0
            print(f"\n[hive] Scanning {sport} (next game in {hours:.1f}h, poll every {(interval or 0) // 60}min) ...")
        else:
            print(f"\n[hive] Scanning {sport} ...")

        all_alerts.extend(_scan_sport(sport, cache_max_age=cache_max_age))

    all_alerts.sort(key=lambda a: a["ev_pct"], reverse=True)
    return all_alerts


# ---------------------------------------------------------------------------
# Strategy 3: Seen-Bets Dedup (seen_bets.json)
# ---------------------------------------------------------------------------


def _seen_bet_hash(alert: dict) -> str:
    """Hash (game + market + book + odds) for dedup."""
    raw = (
        f"{alert['matchup']}|{alert['market']}|{alert['selection']}"
        f"|{alert['book_key']}|{alert['offered_odds_am']}"
    )
    return hashlib.md5(raw.encode()).hexdigest()


def _prune_seen_bets(seen: dict) -> dict:
    """Remove entries older than SEEN_BETS_TTL_MINUTES."""
    cutoff = time.time() - SEEN_BETS_TTL_MINUTES * 60
    return {k: v for k, v in seen.items() if v.get("ts", 0) > cutoff}


def filter_new_alerts(alerts: List[dict]) -> List[dict]:
    """Return only alerts not already in the seen-bets cache."""
    seen = _prune_seen_bets(_load_json(SEEN_BETS_PATH))
    new_alerts = []
    for alert in alerts:
        h = _seen_bet_hash(alert)
        if h not in seen:
            new_alerts.append(alert)
            seen[h] = {"ts": time.time()}
    _save_json(SEEN_BETS_PATH, seen)
    return new_alerts


# ---------------------------------------------------------------------------
# Discord Formatting
# ---------------------------------------------------------------------------


def _sport_display(sport_key: str) -> str:
    mapping = {
        "basketball_nba": "NBA",
        "basketball_wnba": "WNBA",
        "baseball_mlb": "MLB",
        "icehockey_nhl": "NHL",
        "americanfootball_nfl": "NFL",
    }
    return mapping.get(sport_key, sport_key.upper())


def _market_display(market_key: str) -> str:
    mapping = {"h2h": "Moneyline", "spreads": "Spread", "totals": "Total"}
    return mapping.get(market_key, market_key.upper())


def build_discord_embed(alert: dict) -> dict:
    """Construct a rich Discord embed for a +EV alert."""
    sport = _sport_display(alert["sport"])
    market = _market_display(alert["market"])
    ev_display = f"{alert['ev_pct'] * 100:.2f}%"
    qk_display = f"{alert['quarter_kelly_pct']:.2f}%"
    true_prob_display = f"{alert['true_prob'] * 100:.1f}%"

    description = (
        f"**Sport:** {sport}\n"
        f"**Matchup:** {alert['matchup']}\n"
        f"**Market:** {market}\n\n"
        f"**The Play**\n"
        f"**Selection:** {alert['selection']}\n"
        f"**Sportsbook:** {alert['book']}  |  **Odds:** {alert['offered_odds_am']}\n\n"
        f"**Sharp Baseline (Pinnacle)**\n"
        f"**Pinnacle Line:** {alert['sharp_odds_am']}\n"
        f"**True Probability:** {true_prob_display}\n"
        f"**Fair Value:** {alert['fair_value_am']}\n\n"
        f"**The Math**\n"
        f"**EV:** {ev_display}\n"
        f"**Recommend:** {qk_display} of bankroll (Quarter-Kelly)"
    )

    return {
        "embeds": [
            {
                "title": "+EV Alert from The Hive",
                "description": description,
                "color": EMBED_COLOR,
                "footer": {"text": "BEE BAKED BETS  |  The Hive +EV Scanner"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
    }


def send_alerts(alerts: List[dict]) -> int:
    """Send Discord webhook embeds for each alert. Returns count sent."""
    if not DISCORD_WEBHOOK_URL:
        print("[hive] DISCORD_WEBHOOK_URL not set - printing alerts to stdout")
        for alert in alerts:
            _print_alert(alert)
        return len(alerts)

    sent = 0
    for alert in alerts:
        payload = build_discord_embed(alert)
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
            if resp.status_code < 400:
                sent += 1
                print(f"[hive] Alert sent: {alert['matchup']} - {alert['selection']} @ {alert['book']} ({alert['ev_pct']*100:.2f}% EV)")
            else:
                print(f"[hive] Discord error {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as exc:
            print(f"[hive] Discord post failed: {exc}")
    return sent


def _print_alert(alert: dict) -> None:
    """Pretty-print a +EV alert to stdout (when no webhook is configured)."""
    sport = _sport_display(alert["sport"])
    print(
        f"\n  +EV ALERT  |  {sport}  |  {alert['matchup']}\n"
        f"  {alert['selection']} @ {alert['book']} {alert['offered_odds_am']}\n"
        f"  Pinnacle: {alert['sharp_odds_am']}  |  True Prob: {alert['true_prob']*100:.1f}%\n"
        f"  Fair Value: {alert['fair_value_am']}  |  EV: {alert['ev_pct']*100:.2f}%\n"
        f"  Quarter-Kelly: {alert['quarter_kelly_pct']:.2f}% of bankroll\n"
    )


# ---------------------------------------------------------------------------
# Main Entry-Point
# ---------------------------------------------------------------------------


def run_once(use_schedule: bool = True) -> int:
    """Execute a single scan cycle. Returns number of alerts sent."""
    print(f"\n{'='*50}")
    print("  BEE BAKED BETS  -  Hive +EV Scan")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  EV threshold: {EV_THRESHOLD*100:.1f}%  |  Sports: {', '.join(SPORTS)}")
    print(f"  Books: {', '.join(sorted(SOFT_BOOKS))}")
    print(f"  Markets: {MARKETS}")
    print(f"{'='*50}")

    schedule = refresh_schedule_cache() if use_schedule else None
    alerts = scan_all_sports(schedule=schedule)
    print(f"\n[hive] Raw +EV opportunities found: {len(alerts)}")

    new_alerts = filter_new_alerts(alerts)
    print(f"[hive] New alerts (after dedup): {len(new_alerts)}")

    if new_alerts:
        sent = send_alerts(new_alerts)
        print(f"[hive] Alerts dispatched: {sent}")
        return sent

    print("[hive] No new +EV alerts this cycle.")
    return 0


def _next_loop_delay(schedule: Optional[dict]) -> int:
    """Compute the optimal delay until the next scan based on game schedule.

    Uses the shortest polling interval across all sports that are in the
    polling window.  Falls back to 300s (5 min) if no schedule is available.
    """
    if not schedule:
        return 300

    min_interval = None
    for sport in SPORTS:
        should, interval, _ = should_poll_sport(schedule, sport)
        if should and interval is not None:
            if min_interval is None or interval < min_interval:
                min_interval = interval

    return min_interval or 300


def run_loop() -> None:
    """Run the scanner in a dynamic loop.

    The loop delay adapts to the game schedule: polls frequently when
    games are imminent, backs off when games are far away.
    """
    print("[hive] Starting dynamic loop mode. Press Ctrl+C to stop.")
    while True:
        try:
            schedule = refresh_schedule_cache()
            run_once(use_schedule=True)
            delay = _next_loop_delay(schedule)
        except KeyboardInterrupt:
            print("\n[hive] Interrupted. Exiting.")
            sys.exit(0)
        except Exception as exc:
            print(f"[hive] Scan error: {exc}")
            delay = 300

        print(f"\n[hive] Next scan in {delay}s ({delay // 60}min) ...")
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            print("\n[hive] Interrupted. Exiting.")
            sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="BEE BAKED BETS - The Hive +EV Scanner")
    parser.add_argument("--loop", action="store_true", help="Run continuously in a dynamic loop")
    parser.add_argument(
        "--no-schedule", action="store_true",
        help="Disable dynamic scheduling (scan all sports every cycle)",
    )
    args = parser.parse_args()

    if args.loop:
        run_loop()
    else:
        run_once(use_schedule=not args.no_schedule)


if __name__ == "__main__":
    main()
