from __future__ import annotations

from execution.models import ParentOrder, Side, VenueQuote
from utils.odds import decimal_to_american


def build_order_from_edge(
    matchup: str,
    market: str,
    selection: str,
    offered_decimal: float,
    fair_decimal: float,
    units: float,
    source_signal: str = "positive_ev",
) -> ParentOrder:
    symbol = f"{matchup} | {market} | {selection}"
    edge = (offered_decimal / fair_decimal) - 1.0
    return ParentOrder(
        symbol=symbol,
        side=Side.BUY,
        quantity=max(0.0, float(units)),
        limit_price=float(offered_decimal),
        fair_price=float(fair_decimal),
        strategy="SMART",
        source_signal=source_signal,
        metadata={
            "matchup": matchup,
            "market": market,
            "selection": selection,
            "edge": edge,
            "price_mode": "higher_is_better",
            "offered_american": decimal_to_american(offered_decimal),
            "fair_american": decimal_to_american(fair_decimal),
        },
    )


def quote_from_book(symbol: str, book_key: str, book_title: str, price: float, capacity: float, weight: float = 1.0) -> VenueQuote:
    return VenueQuote(
        venue_id=book_key,
        symbol=symbol,
        ask_price=float(price),
        available_quantity=float(capacity),
        fee_bps=0.0,
        latency_ms=120,
        fill_probability=max(0.1, min(0.99, 0.72 + (float(weight) * 0.1))),
        metadata={"book": book_title, "book_weight": weight},
    )
