#!/usr/bin/env python3
"""Summarize execution quality by venue."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

from db_manager import get_venue_metrics


def summarize_venue_metrics(rows: Iterable[dict]) -> List[dict]:
    grouped: Dict[str, dict] = defaultdict(
        lambda: {
            "venue_id": "",
            "orders": 0,
            "routed_quantity": 0.0,
            "filled_quantity": 0.0,
            "notional": 0.0,
            "fees": 0.0,
            "edge_capture_sum": 0.0,
            "edge_capture_count": 0,
        }
    )
    for row in rows:
        venue_id = str(row.get("venue_id") or "unknown")
        routed_qty = float(row.get("routed_quantity") or 0.0)
        filled_qty = float(row.get("filled_quantity") or 0.0)
        avg_price = row.get("average_fill_price")
        edge_capture = row.get("edge_capture")
        bucket = grouped[venue_id]
        bucket["venue_id"] = venue_id
        bucket["orders"] += 1
        bucket["routed_quantity"] += routed_qty
        bucket["filled_quantity"] += filled_qty
        bucket["fees"] += float(row.get("fee") or 0.0)
        if avg_price is not None:
            bucket["notional"] += filled_qty * float(avg_price)
        if edge_capture is not None:
            bucket["edge_capture_sum"] += float(edge_capture)
            bucket["edge_capture_count"] += 1

    summaries = []
    for bucket in grouped.values():
        filled_qty = bucket["filled_quantity"]
        routed_qty = bucket["routed_quantity"]
        summaries.append(
            {
                "venue_id": bucket["venue_id"],
                "orders": bucket["orders"],
                "routed_quantity": round(routed_qty, 6),
                "filled_quantity": round(filled_qty, 6),
                "fill_rate": round(filled_qty / routed_qty, 6) if routed_qty else 0.0,
                "average_fill_price": round(bucket["notional"] / filled_qty, 6) if filled_qty else None,
                "average_edge_capture": (
                    round(bucket["edge_capture_sum"] / bucket["edge_capture_count"], 6)
                    if bucket["edge_capture_count"]
                    else None
                ),
                "fees": round(bucket["fees"], 6),
            }
        )
    return sorted(summaries, key=lambda row: (row["average_edge_capture"] or 0, row["fill_rate"]), reverse=True)


def _rows_from_ledger(path: Path) -> List[dict]:
    if not path.exists():
        return []
    reports = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for report in reports if isinstance(reports, list) else []:
        metrics = report.get("metrics", {})
        fills_by_child = defaultdict(list)
        for fill in report.get("fills", []):
            fills_by_child[fill.get("child_order_id")].append(fill)
        for child in report.get("child_orders", []):
            child_fills = fills_by_child.get(child.get("child_order_id"), [])
            filled_qty = sum(float(fill.get("quantity") or 0.0) for fill in child_fills)
            notional = sum(float(fill.get("quantity") or 0.0) * float(fill.get("price") or 0.0) for fill in child_fills)
            rows.append(
                {
                    "venue_id": child.get("venue_id"),
                    "routed_quantity": child.get("quantity"),
                    "filled_quantity": filled_qty,
                    "average_fill_price": (notional / filled_qty) if filled_qty else None,
                    "fee": sum(float(fill.get("fee") or 0.0) for fill in child_fills),
                    "edge_capture": metrics.get("edge_capture"),
                }
            )
    return rows


def main() -> List[dict]:
    parser = argparse.ArgumentParser(description="Summarize execution quality by venue.")
    parser.add_argument("--ledger", type=Path, help="Read from local execution_ledger.json instead of Supabase.")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    rows = _rows_from_ledger(args.ledger) if args.ledger else get_venue_metrics(args.limit)
    summary = summarize_venue_metrics(rows)
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
