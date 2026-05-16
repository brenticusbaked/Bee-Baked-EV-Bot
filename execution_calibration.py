#!/usr/bin/env python3
"""Write a synthetic execution report to validate Supabase persistence without odds API calls."""

from __future__ import annotations

import json

from db_manager import log_execution_report_to_db
from execution_scanner import _synthetic_calibration_report


def run_execution_calibration() -> dict:
    report = _synthetic_calibration_report()
    success = log_execution_report_to_db(report)
    result = {
        "detail": "synthetic execution calibration wrote to Supabase" if success else "synthetic execution calibration queued locally",
        "count": 1,
        "label": "executions",
        "success": success,
        "order_id": report["parent_order"]["order_id"],
        "source_signal": report["parent_order"]["source_signal"],
    }
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    run_execution_calibration()

