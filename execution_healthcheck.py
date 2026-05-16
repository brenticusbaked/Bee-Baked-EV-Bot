#!/usr/bin/env python3
"""Check whether the execution desk wrote post-run rows to Supabase."""

from __future__ import annotations

import argparse
import json
from typing import Dict, List

from db_manager import get_latest_rows, get_table_count
from execution_analytics import summarize_venue_metrics


EXECUTION_TABLES = {
    "execution_orders": "logged_at",
    "execution_child_orders": "logged_at",
    "execution_fills": "filled_at",
    "venue_metrics": "measured_at",
}


def build_healthcheck(limit: int = 5) -> Dict[str, object]:
    tables: Dict[str, dict] = {}
    for table_name, order_column in EXECUTION_TABLES.items():
        latest = get_latest_rows(table_name, order_column, limit=limit)
        tables[table_name] = {
            "count": get_table_count(table_name),
            "latest": latest,
        }

    venue_summary = summarize_venue_metrics(tables["venue_metrics"]["latest"])
    return {
        "ok": all(table["count"] > 0 for table in tables.values()),
        "tables": tables,
        "venue_summary_from_latest_rows": venue_summary,
    }


def main() -> Dict[str, object]:
    parser = argparse.ArgumentParser(description="Validate execution desk Supabase persistence.")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    result = build_healthcheck(limit=args.limit)
    print(json.dumps(result, indent=2, default=str))
    return result


if __name__ == "__main__":
    main()
