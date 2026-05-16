from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from db_manager import get_master_cache
from execution.desk import ExecutionDesk, report_to_dict
from execution.risk import RiskLimits, RiskManager
from execution_signal import build_order_from_edge, quote_from_book
from services.book_weights import get_book_weights
from utils.odds import fair_probabilities_from_prices, quarter_kelly_units
from utils.thresholds import env_float, env_int


EXECUTION_EV_THRESHOLD = env_float("EXECUTION_EV_THRESHOLD", 0.025)
EXECUTION_MAX_ORDERS = max(1, env_int("EXECUTION_MAX_ORDERS", 10))
EXECUTION_MAX_ORDER_UNITS = env_float("EXECUTION_MAX_ORDER_UNITS", 5.0)
EXECUTION_MAX_NOTIONAL = env_float("EXECUTION_MAX_NOTIONAL", 1000.0)
EXECUTION_LEDGER_PATH = os.getenv("EXECUTION_LEDGER_PATH", "execution_ledger.json")


def _outcome_key(outcome: dict) -> Tuple[str, str]:
    return (str(outcome["name"]).lower().strip(), str(outcome.get("point", "")))


def _selection_text(outcome: dict) -> str:
    return f"{outcome['name']} {outcome.get('point', '')}".strip()


def _calculate_edge_from_probability(offered_price: float, fair_probability: float) -> float:
    return (float(offered_price) * float(fair_probability)) - 1.0


def _append_reports(path: str, reports: List[dict]) -> None:
    if not reports:
        return
    ledger_path = Path(path)
    existing = []
    if ledger_path.exists():
        try:
            existing = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    if not isinstance(existing, list):
        existing = []
    existing.extend(reports)
    ledger_path.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")


def run_execution_scan() -> dict:
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return {"detail": "cache empty", "count": 0, "label": "executions"}

    book_weights = get_book_weights()
    soft_books = {"fanduel", "draftkings", "betmgm", "bet365", "caesars", "bovada"}
    now = datetime.now(timezone.utc)
    reports: List[dict] = []

    for sport, events in cache.items():
        for event in events:
            if len(reports) >= EXECUTION_MAX_ORDERS:
                break
            commence_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
            if now > commence_time:
                continue

            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets: Dict[str, dict] = {}
            for bookmaker in event.get("bookmakers", []):
                book_key = bookmaker.get("key")
                for market in bookmaker.get("markets", []):
                    market_key = market["key"]
                    markets.setdefault(market_key, {"sharp": {}, "venues": {}})
                    if book_key == "pinnacle":
                        for outcome in market.get("outcomes", []):
                            markets[market_key]["sharp"][_outcome_key(outcome)] = float(outcome["price"])
                    elif book_key in soft_books:
                        for outcome in market.get("outcomes", []):
                            markets[market_key]["venues"].setdefault(_outcome_key(outcome), []).append(
                                {
                                    "book_key": book_key,
                                    "book": bookmaker.get("title", book_key),
                                    "price": float(outcome["price"]),
                                    "selection": _selection_text(outcome),
                                    "capacity": EXECUTION_MAX_ORDER_UNITS,
                                    "weight": book_weights.get(bookmaker.get("title", book_key), 1.0),
                                }
                            )

            for market_type, market_data in markets.items():
                if len(reports) >= EXECUTION_MAX_ORDERS:
                    break
                fair_probs = fair_probabilities_from_prices(market_data["sharp"])
                for key, venues in market_data["venues"].items():
                    if len(reports) >= EXECUTION_MAX_ORDERS:
                        break
                    fair_probability = fair_probs.get(key)
                    if not fair_probability:
                        continue

                    best = max(venues, key=lambda venue: venue["price"])
                    edge = _calculate_edge_from_probability(best["price"], fair_probability)
                    if edge < EXECUTION_EV_THRESHOLD:
                        continue

                    fair_decimal = 1.0 / fair_probability
                    units = quarter_kelly_units(edge, best["price"], cap=EXECUTION_MAX_ORDER_UNITS)
                    if units <= 0:
                        continue

                    order = build_order_from_edge(
                        matchup=matchup,
                        market=market_type,
                        selection=best["selection"],
                        offered_decimal=best["price"],
                        fair_decimal=fair_decimal,
                        units=units,
                        source_signal=f"{sport}:{event.get('id')}",
                    )
                    quotes = [
                        quote_from_book(order.symbol, venue["book_key"], venue["book"], venue["price"], units, venue["weight"])
                        for venue in venues
                    ]
                    risk = RiskManager(RiskLimits(EXECUTION_MAX_ORDER_UNITS, EXECUTION_MAX_NOTIONAL, EXECUTION_EV_THRESHOLD))
                    report = ExecutionDesk.paper(quotes, risk=risk).execute(order)
                    reports.append(report_to_dict(report))

    _append_reports(EXECUTION_LEDGER_PATH, reports)
    detail = f"paper routed {len(reports)} order(s)"
    print(detail)
    return {
        "detail": detail,
        "count": len(reports),
        "label": "executions",
        "meta": {"execution_ledger_path": EXECUTION_LEDGER_PATH},
    }


if __name__ == "__main__":
    print(json.dumps(run_execution_scan(), indent=2))
