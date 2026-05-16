from __future__ import annotations

from typing import Iterable

from execution.models import Fill, ParentOrder


def execution_metrics(order: ParentOrder, fills: Iterable[Fill]) -> dict:
    fill_list = list(fills)
    total_qty = sum(fill.quantity for fill in fill_list)
    if total_qty <= 0:
        return {
            "filled_quantity": 0.0,
            "fill_rate": 0.0,
            "average_price": 0.0,
            "slippage": 0.0,
            "edge_capture": 0.0,
            "fees": 0.0,
        }

    notional = sum(fill.quantity * fill.price for fill in fill_list)
    fees = sum(fill.fee for fill in fill_list)
    average_price = notional / total_qty
    benchmark = order.fair_price or order.limit_price or average_price
    higher_is_better = order.metadata.get("price_mode") == "higher_is_better"
    slippage = (benchmark - average_price) if higher_is_better else (average_price - benchmark)
    edge_capture = ((average_price - benchmark) / benchmark) if higher_is_better and benchmark else 0.0
    if not higher_is_better and benchmark:
        edge_capture = (benchmark - average_price) / benchmark
    return {
        "filled_quantity": round(total_qty, 6),
        "fill_rate": round(total_qty / order.quantity, 6),
        "average_price": round(average_price, 6),
        "slippage": round(slippage, 6),
        "edge_capture": round(edge_capture, 6),
        "fees": round(fees, 6),
    }
