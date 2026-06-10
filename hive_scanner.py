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

Run once:   python hive_scanner.py
Loop mode:  python hive_scanner.py --loop --interval 300
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

# Markets to scan per sport.
MARKETS = os.getenv("HIVE_MARKETS", "h2h,spreads,totals")

# US recreational sportsbooks we compare against Pinnacle.
SOFT_BOOKS = {
    b.strip()
    for b in os.getenv(
        "HIVE_SOFT_BOOKS", "draftkings,fanduel,betmgm,caesars,bet365"
    ).split(",")
    if b.strip()
}

# De-vig method: "multiplicative" or "power" (default).
DEVIG_METHOD = os.getenv("HIVE_DEVIG_METHOD", "power")

# Quarter-Kelly cap (max bankroll % per bet).
KELLY_CAP = float(os.getenv("HIVE_KELLY_CAP", "5.0"))

# Local JSON file for alert dedup.
ALERT_CACHE_PATH = os.getenv("HIVE_ALERT_CACHE", "hive_alert_cache.json")

# How many hours to keep entries in the dedup cache.
CACHE_TTL_HOURS = float(os.getenv("HIVE_CACHE_TTL_HOURS", "12"))

# Embed colour (green).
EMBED_COLOR = 0x2ECC71

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
# Data Ingestion
# ---------------------------------------------------------------------------


def _fetch_odds(sport: str, region: str, bookmakers: Optional[str] = None) -> List[dict]:
    """Pull odds from The Odds API for a single sport + region."""
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
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"[hive] {sport} ({region}): {len(resp.json())} events  |  API credits left: {remaining}")
        return resp.json()
    except requests.RequestException as exc:
        print(f"[hive] API error for {sport}/{region}: {exc}")
        return []


def fetch_sharp_and_soft(sport: str) -> Tuple[List[dict], List[dict]]:
    """Two API calls per sport: EU (Pinnacle) and US (soft books)."""
    sharp_events = _fetch_odds(sport, "eu", bookmakers="pinnacle")
    soft_events = _fetch_odds(sport, "us", bookmakers=",".join(SOFT_BOOKS))
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


def _scan_sport(sport: str) -> List[dict]:
    """Scan one sport: fetch data, de-vig Pinnacle, compare soft books."""
    sharp_events, soft_events = fetch_sharp_and_soft(sport)
    if not sharp_events or not soft_events:
        return []

    pinnacle = _extract_pinnacle_lines(sharp_events)

    # Build event metadata lookup from sharp events (for matchup info).
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


def scan_all_sports() -> List[dict]:
    """Run the +EV scan across all configured sports."""
    all_alerts = []
    for sport in SPORTS:
        print(f"\n[hive] Scanning {sport} ...")
        all_alerts.extend(_scan_sport(sport))
    all_alerts.sort(key=lambda a: a["ev_pct"], reverse=True)
    return all_alerts


# ---------------------------------------------------------------------------
# Alert Dedup Cache (JSON file)
# ---------------------------------------------------------------------------


def _load_cache() -> dict:
    if not os.path.exists(ALERT_CACHE_PATH):
        return {}
    try:
        with open(ALERT_CACHE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(ALERT_CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except OSError as exc:
        print(f"[hive] Cache save error: {exc}")


def _cache_key(alert: dict) -> str:
    raw = f"{alert['matchup']}|{alert['market']}|{alert['selection']}|{alert['book_key']}"
    return hashlib.md5(raw.encode()).hexdigest()


def _prune_cache(cache: dict) -> dict:
    """Remove entries older than CACHE_TTL_HOURS."""
    now = time.time()
    cutoff = now - CACHE_TTL_HOURS * 3600
    return {k: v for k, v in cache.items() if v.get("ts", 0) > cutoff}


def filter_new_alerts(alerts: List[dict]) -> List[dict]:
    """Return only alerts not already in the dedup cache, then update cache."""
    cache = _prune_cache(_load_cache())
    new_alerts = []
    for alert in alerts:
        key = _cache_key(alert)
        if key not in cache:
            new_alerts.append(alert)
            cache[key] = {"ts": time.time()}
    _save_cache(cache)
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


def run_once() -> int:
    """Execute a single scan cycle. Returns number of alerts sent."""
    print(f"\n{'='*50}")
    print("  BEE BAKED BETS  -  Hive +EV Scan")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  EV threshold: {EV_THRESHOLD*100:.1f}%  |  Sports: {', '.join(SPORTS)}")
    print(f"{'='*50}")

    alerts = scan_all_sports()
    print(f"\n[hive] Raw +EV opportunities found: {len(alerts)}")

    new_alerts = filter_new_alerts(alerts)
    print(f"[hive] New alerts (after dedup): {len(new_alerts)}")

    if new_alerts:
        sent = send_alerts(new_alerts)
        print(f"[hive] Alerts dispatched: {sent}")
        return sent

    print("[hive] No new +EV alerts this cycle.")
    return 0


def run_loop(interval: int = 300) -> None:
    """Run the scanner in a continuous loop."""
    print(f"[hive] Starting loop mode (interval={interval}s). Press Ctrl+C to stop.")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\n[hive] Interrupted. Exiting.")
            sys.exit(0)
        except Exception as exc:
            print(f"[hive] Scan error: {exc}")
        print(f"\n[hive] Sleeping {interval}s until next scan ...")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\n[hive] Interrupted. Exiting.")
            sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="BEE BAKED BETS - The Hive +EV Scanner")
    parser.add_argument("--loop", action="store_true", help="Run continuously in a loop")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between scans (default 300)")
    args = parser.parse_args()

    if args.loop:
        run_loop(interval=args.interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
