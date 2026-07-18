from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from db_manager import get_master_cache, get_venue_metrics, log_execution_report_to_db
from execution.desk import ExecutionDesk, report_to_dict
from execution.models import ParentOrder, Side, VenueQuote
from execution.risk import RiskLimits, RiskManager
from execution.router import SmartOrderRouter
from execution.venue_scores import build_venue_scores
from execution_signal import build_order_from_edge, quote_from_book
from services.book_weights import get_book_weights
from utils.odds import fair_probabilities_from_prices, quarter_kelly_units
from utils.scratch_guard import filter_valid_events, validate_bookmaker_outcomes
from utils.thresholds import env_float, env_int


EXECUTION_EV_THRESHOLD = env_float("EXECUTION_EV_THRESHOLD", 0.025)
EXECUTION_MAX_ORDERS = max(1, env_int("EXECUTION_MAX_ORDERS", 10))
EXECUTION_CALIBRATION_ORDERS = max(0, env_int("EXECUTION_CALIBRATION_ORDERS", 0))
EXECUTION_MAX_ORDER_UNITS = env_float("EXECUTION_MAX_ORDER_UNITS", 5.0)
EXECUTION_MAX_NOTIONAL = env_float("EXECUTION_MAX_NOTIONAL", 1000.0)
EXECUTION_LEDGER_PATH = os.getenv("EXECUTION_LEDGER_PATH", "execution_ledger.json")
ENABLE_ADAPTIVE_VENUE_SCORING = os.getenv("ENABLE_ADAPTIVE_VENUE_SCORING", "true").strip().lower() in {"1", "true", "yes", "on"}
DEVIG_METHOD = os.getenv("DEVIG_METHOD", "power")
ENABLE_SHIN_DEVIG = os.getenv("ENABLE_SHIN_DEVIG", "").strip().lower() in {"1", "true", "yes", "on"}
EXECUTION_VENUE_SCORE_LOOKBACK = max(1, env_int("EXECUTION_VENUE_SCORE_LOOKBACK", 500))
EXECUTION_VENUE_SCORE_MIN_SAMPLE = max(1, env_int("EXECUTION_VENUE_SCORE_MIN_SAMPLE", 3))


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


def _synthetic_calibration_report() -> dict:
    order = ParentOrder(
        symbol="EXECUTION_DESK_HEALTHCHECK | calibration | persistence",
        side=Side.BUY,
        quantity=0.25,
        limit_price=2.0,
        fair_price=1.99,
        strategy="SMART",
        source_signal="synthetic_calibration",
        metadata={
            "edge": 0.005025,
            "price_mode": "higher_is_better",
            "calibration": True,
            "synthetic": True,
        },
    )
    quotes = [
        VenueQuote(
            venue_id="paper_healthcheck",
            symbol=order.symbol,
            ask_price=2.0,
            available_quantity=0.25,
            latency_ms=1,
            fill_probability=1.0,
        )
    ]
    risk = RiskManager(RiskLimits(EXECUTION_MAX_ORDER_UNITS, EXECUTION_MAX_NOTIONAL, 0.0))
    return report_to_dict(ExecutionDesk.paper(quotes, risk=risk).execute(order))


def run_execution_scan() -> dict:
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return {"detail": "cache empty", "count": 0, "label": "executions"}

    book_weights = get_book_weights()
    venue_scores = {}
    if ENABLE_ADAPTIVE_VENUE_SCORING:
        scored = build_venue_scores(
            get_venue_metrics(EXECUTION_VENUE_SCORE_LOOKBACK),
            min_sample=EXECUTION_VENUE_SCORE_MIN_SAMPLE,
        )
        venue_scores = {venue_id: score.score for venue_id, score in scored.items()}
        print(f"execution_desk adaptive venue scores loaded: {venue_scores}")
    soft_books = {
        "fanduel", "draftkings", "betmgm", "bet365", "caesars", "bovada",
        "novig", "kalshi", "polymarket", "prophetx",
    }
    reports: List[dict] = []
    candidates: List[dict] = []

    for sport, events in cache.items():
        for event in filter_valid_events(events, sport):
            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets: Dict[str, dict] = {}
            for bookmaker in event.get("bookmakers", []):
                if not validate_bookmaker_outcomes(bookmaker):
                    continue
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
                devig_method = "shin" if ENABLE_SHIN_DEVIG else DEVIG_METHOD
                fair_probs = fair_probabilities_from_prices(market_data["sharp"], method=devig_method)
                for key, venues in market_data["venues"].items():
                    fair_probability = fair_probs.get(key)
                    if not fair_probability:
                        continue

                    best = max(venues, key=lambda venue: venue["price"])
                    edge = _calculate_edge_from_probability(best["price"], fair_probability)
                    fair_decimal = 1.0 / fair_probability
                    units = quarter_kelly_units(max(edge, 0.005), best["price"], cap=EXECUTION_MAX_ORDER_UNITS)
                    candidates.append(
                        {
                            "edge": edge,
                            "sport": sport,
                            "event_id": event.get("id"),
                            "matchup": matchup,
                            "market_type": market_type,
                            "best": best,
                            "venues": venues,
                            "fair_decimal": fair_decimal,
                            "units": max(0.25, units),
                        }
                    )

    selected = [candidate for candidate in candidates if candidate["edge"] >= EXECUTION_EV_THRESHOLD]
    selected = sorted(selected, key=lambda candidate: candidate["edge"], reverse=True)[:EXECUTION_MAX_ORDERS]
    if len(selected) < EXECUTION_MAX_ORDERS and EXECUTION_CALIBRATION_ORDERS:
        selected_ids = {(item["event_id"], item["market_type"], item["best"]["selection"]) for item in selected}
        calibration_slots = min(EXECUTION_CALIBRATION_ORDERS, EXECUTION_MAX_ORDERS - len(selected))
        calibration_candidates = [
            candidate for candidate in sorted(candidates, key=lambda item: item["edge"], reverse=True)
            if (candidate["event_id"], candidate["market_type"], candidate["best"]["selection"]) not in selected_ids
        ][:calibration_slots]
        for candidate in calibration_candidates:
            candidate["calibration"] = True
        selected.extend(calibration_candidates)

    print(
        "execution_desk candidates: "
        f"{len(candidates)} total | {len(selected)} selected | "
        f"calibration_slots={EXECUTION_CALIBRATION_ORDERS}"
    )
    for index, candidate in enumerate(sorted(candidates, key=lambda item: item["edge"], reverse=True)[:5], start=1):
        print(
            "execution_desk top candidate "
            f"{index}: edge={candidate['edge']:.4%} | "
            f"{candidate['sport']} | {candidate['market_type']} | "
            f"{candidate['matchup']} | {candidate['best']['selection']} @ {candidate['best']['price']}"
        )

    for candidate in selected:
        best = candidate["best"]
        order = build_order_from_edge(
            matchup=candidate["matchup"],
            market=candidate["market_type"],
            selection=best["selection"],
            offered_decimal=best["price"],
            fair_decimal=candidate["fair_decimal"],
            units=candidate["units"],
            source_signal=f"{candidate['sport']}:{candidate['event_id']}",
        )
        order.metadata["edge"] = candidate["edge"]
        if candidate.get("calibration"):
            order.metadata["calibration"] = True
        quotes = [
            quote_from_book(order.symbol, venue["book_key"], venue["book"], venue["price"], candidate["units"], venue["weight"])
            for venue in candidate["venues"]
        ]
        min_edge = min(EXECUTION_EV_THRESHOLD, candidate["edge"]) if candidate.get("calibration") else EXECUTION_EV_THRESHOLD
        risk = RiskManager(RiskLimits(EXECUTION_MAX_ORDER_UNITS, EXECUTION_MAX_NOTIONAL, min_edge))
        router = SmartOrderRouter(venue_scores=venue_scores)
        report = report_to_dict(ExecutionDesk.paper(quotes, risk=risk, router=router).execute(order))
        log_execution_report_to_db(report)
        reports.append(report)

    if not reports and EXECUTION_CALIBRATION_ORDERS:
        report = _synthetic_calibration_report()
        log_execution_report_to_db(report)
        reports.append(report)
        print("execution_desk wrote synthetic calibration order for persistence healthcheck")

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
