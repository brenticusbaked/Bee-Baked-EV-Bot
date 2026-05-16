#!/usr/bin/env python3
"""Paper execution management system and smart order router demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from execution.desk import ExecutionDesk, report_to_dict
from execution.models import ParentOrder, Side, VenueQuote
from execution.risk import RiskLimits, RiskManager


def load_order(path: Path) -> ParentOrder:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ParentOrder(
        symbol=data["symbol"],
        side=Side(data.get("side", "BUY")),
        quantity=float(data["quantity"]),
        limit_price=float(data["limit_price"]) if data.get("limit_price") is not None else None,
        fair_price=float(data["fair_price"]) if data.get("fair_price") is not None else None,
        strategy=data.get("strategy", "SMART"),
        source_signal=data.get("source_signal", "manual"),
        metadata=data.get("metadata", {}),
    )


def load_quotes(path: Path) -> list[VenueQuote]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [VenueQuote(**row) for row in rows]


def default_order() -> ParentOrder:
    return ParentOrder(
        symbol="Lakers @ Bulls | spreads | Bulls -2.5",
        side=Side.BUY,
        quantity=3.0,
        limit_price=1.95,
        fair_price=1.86,
        source_signal="demo_positive_ev",
        metadata={"edge": 0.0484, "price_mode": "higher_is_better"},
    )


def default_quotes(symbol: str) -> list[VenueQuote]:
    return [
        VenueQuote("fanduel", symbol, ask_price=1.95, available_quantity=1.5, latency_ms=95, fill_probability=0.92),
        VenueQuote("draftkings", symbol, ask_price=1.93, available_quantity=1.0, latency_ms=110, fill_probability=0.89),
        VenueQuote("betmgm", symbol, ask_price=1.91, available_quantity=3.0, latency_ms=180, fill_probability=0.86),
    ]


def main() -> dict:
    parser = argparse.ArgumentParser(description="Run the paper EMS/SOR execution desk.")
    parser.add_argument("--order-json", type=Path, help="Parent order JSON file.")
    parser.add_argument("--quotes-json", type=Path, help="Venue quote JSON list.")
    parser.add_argument("--max-order-quantity", type=float, default=10.0)
    parser.add_argument("--max-notional", type=float, default=1000.0)
    parser.add_argument("--min-edge", type=float, default=0.0)
    args = parser.parse_args()

    order = load_order(args.order_json) if args.order_json else default_order()
    quotes = load_quotes(args.quotes_json) if args.quotes_json else default_quotes(order.symbol)
    risk = RiskManager(RiskLimits(args.max_order_quantity, args.max_notional, args.min_edge))
    report = ExecutionDesk.paper(quotes, risk=risk).execute(order)
    payload = report_to_dict(report)
    print(json.dumps(payload, indent=2, default=str))
    return payload


if __name__ == "__main__":
    main()
