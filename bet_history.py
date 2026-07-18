"""Personal betting-history ingestion and calibration summary.

Loads the user's exported sportsbook transactions (CSV), normalizes them into a
consistent schema, and produces a compact aggregate summary used to calibrate
the decision engine against *realized* results:

  * realized ROI by book        -> history-weighted book scores
  * realized ROI by EV bucket    -> validates/anchors the EV floor
  * realized CLV by book/market  -> baseline for the CLV tracker

Design notes / guardrails:
  * This layer NEVER feeds fair-value probabilities. Pinnacle remains the sole
    fair-market baseline (see .windsurfrules Rule 1). History only adjusts
    execution-side weighting, thresholds, and CLV context.
  * The raw CSV and the derived summary contain sensitive personal financial
    data, so both are git-ignored. Only aggregates are persisted.
  * No look-ahead: aggregates are historical priors, not per-bet outcomes.
"""

import csv
import json
import math
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from utils.book_names import normalize_book

BET_HISTORY_CSV = os.getenv("BET_HISTORY_CSV", "bet_history.csv")
BET_HISTORY_SUMMARY_PATH = os.getenv("BET_HISTORY_SUMMARY_PATH", "bet_history_summary.json")

# Status -> normalized settlement result.
_STATUS_RESULT = {
    "SETTLED_WIN": "won",
    "SETTLED_LOSS": "lost",
    "SETTLED_PUSH": "push",
    "SETTLED_CASH_OUT": "cashout",
    "SETTLED_VOID": "void",
    "SETTLED": "unknown",
    "PLACED": "pending",
}

# Results that represent a resolved money outcome (count toward ROI).
_ROI_RESULTS = {"won", "lost", "push", "cashout"}

# EV buckets used for realized-ROI calibration (lower bound inclusive).
_EV_BUCKETS = (
    ("neg", float("-inf")),
    ("0-2%", 0.0),
    ("2-5%", 0.02),
    ("5-10%", 0.05),
    ("10%+", 0.10),
)


def _to_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed


def _ev_bucket(ev: Optional[float]) -> Optional[str]:
    if ev is None:
        return None
    label = "neg"
    for name, lower in _EV_BUCKETS:
        if ev >= lower:
            label = name
    return label


def normalize_transaction(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize a single raw CSV row into a consistent record, or None."""
    raw_book = (row.get("sportsbook") or "").strip()
    status = (row.get("status") or "").strip()
    if not raw_book and not status:
        return None
    return {
        "bet_id": (row.get("bet_id") or "").strip(),
        "book": normalize_book(raw_book),
        "raw_book": raw_book,
        "bet_type": (row.get("type") or "").strip(),
        "status": status,
        "result": _STATUS_RESULT.get(status, "unknown"),
        "odds_decimal": _to_float(row.get("odds")),
        "closing_line_decimal": _to_float(row.get("closing_line")),
        "predicted_ev": _to_float(row.get("ev")),
        "stake": _to_float(row.get("amount")),
        "profit": _to_float(row.get("profit")),
        "placed_at": (row.get("time_placed_iso") or "").strip() or None,
        "settled_at": (row.get("time_settled_iso") or "").strip() or None,
        "sport": (row.get("sports") or "").strip(),
        "league": (row.get("leagues") or "").strip(),
        "tags": [tag for tag in (row.get("tags") or "").split() if tag],
    }


def parse_transactions_csv(path: str) -> List[Dict[str, Any]]:
    """Parse an exported transactions CSV into normalized records."""
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            record = normalize_transaction(row)
            if record is not None:
                records.append(record)
    return records


def _roi(stake: float, profit: float) -> float:
    return (profit / stake) if stake else 0.0


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate normalized records into calibration statistics."""
    settled = [
        r
        for r in records
        if r["result"] in _ROI_RESULTS and r["stake"] is not None and r["profit"] is not None
    ]

    overall_stake = sum(r["stake"] for r in settled)
    overall_profit = sum(r["profit"] for r in settled)

    by_book_stake: Dict[str, float] = defaultdict(float)
    by_book_profit: Dict[str, float] = defaultdict(float)
    by_book_n: Dict[str, int] = defaultdict(int)
    for r in settled:
        by_book_stake[r["book"]] += r["stake"]
        by_book_profit[r["book"]] += r["profit"]
        by_book_n[r["book"]] += 1

    by_book = {
        book: {
            "n": by_book_n[book],
            "stake": round(by_book_stake[book], 2),
            "profit": round(by_book_profit[book], 2),
            "roi": round(_roi(by_book_stake[book], by_book_profit[book]), 4),
        }
        for book in by_book_n
    }

    ev_stake: Dict[str, float] = defaultdict(float)
    ev_profit: Dict[str, float] = defaultdict(float)
    ev_n: Dict[str, int] = defaultdict(int)
    for r in settled:
        bucket = _ev_bucket(r["predicted_ev"])
        if bucket is None:
            continue
        ev_stake[bucket] += r["stake"]
        ev_profit[bucket] += r["profit"]
        ev_n[bucket] += 1
    ev_buckets = {
        bucket: {
            "n": ev_n[bucket],
            "roi": round(_roi(ev_stake[bucket], ev_profit[bucket]), 4),
        }
        for bucket in ev_n
    }

    type_stake: Dict[str, float] = defaultdict(float)
    type_profit: Dict[str, float] = defaultdict(float)
    type_n: Dict[str, int] = defaultdict(int)
    for r in settled:
        type_stake[r["bet_type"]] += r["stake"]
        type_profit[r["bet_type"]] += r["profit"]
        type_n[r["bet_type"]] += 1
    by_type = {
        bet_type: {
            "n": type_n[bet_type],
            "roi": round(_roi(type_stake[bet_type], type_profit[bet_type]), 4),
        }
        for bet_type in type_n
    }

    clv_sum: Dict[str, float] = defaultdict(float)
    clv_beat: Dict[str, int] = defaultdict(int)
    clv_n: Dict[str, int] = defaultdict(int)
    for r in settled:
        odds = r["odds_decimal"]
        closing = r["closing_line_decimal"]
        if not odds or not closing or closing <= 1.0:
            continue
        edge_pct = (odds / closing - 1.0) * 100.0
        clv_sum[r["book"]] += edge_pct
        clv_beat[r["book"]] += 1 if odds > closing else 0
        clv_n[r["book"]] += 1
    clv_by_book = {
        book: {
            "n": clv_n[book],
            "avg_clv_pct": round(clv_sum[book] / clv_n[book], 4),
            "pct_beat_close": round(clv_beat[book] / clv_n[book], 4),
        }
        for book in clv_n
    }

    return {
        "overall": {
            "n": len(settled),
            "stake": round(overall_stake, 2),
            "profit": round(overall_profit, 2),
            "roi": round(_roi(overall_stake, overall_profit), 4),
        },
        "by_book": by_book,
        "ev_buckets": ev_buckets,
        "by_type": by_type,
        "clv_by_book": clv_by_book,
    }


def write_summary(summary: Dict[str, Any], path: str = BET_HISTORY_SUMMARY_PATH) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)


def load_local_summary(path: str = BET_HISTORY_SUMMARY_PATH) -> Optional[Dict[str, Any]]:
    """Load the cached calibration summary from disk, or None if absent."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def save_summary_to_supabase(summary: Dict[str, Any]) -> bool:
    """Upsert the aggregate summary into Supabase `bet_history_summary`."""
    from db_manager import supabase

    if not supabase:
        return False
    try:
        supabase.table("bet_history_summary").upsert(
            {"id": "latest", "summary": summary}, on_conflict="id"
        ).execute()
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort persistence
        print(f"[bet_history] summary upload failed: {exc}")
        return False


def load_summary_from_supabase() -> Optional[Dict[str, Any]]:
    """Load the aggregate summary from Supabase, or None if unavailable."""
    from db_manager import supabase

    if not supabase:
        return None
    try:
        rows = (
            supabase.table("bet_history_summary")
            .select("summary")
            .eq("id", "latest")
            .limit(1)
            .execute()
            .data
        )
    except Exception as exc:  # noqa: BLE001 - best-effort read
        print(f"[bet_history] summary fetch failed: {exc}")
        return None
    if rows and isinstance(rows[0].get("summary"), dict):
        return rows[0]["summary"]
    return None


def load_summary(path: str = BET_HISTORY_SUMMARY_PATH) -> Optional[Dict[str, Any]]:
    """Load the calibration summary: local cache first, then Supabase."""
    return load_local_summary(path) or load_summary_from_supabase()


def load_csv_to_supabase(path: str, batch_size: int = 500) -> int:
    """Insert normalized history rows into Supabase `bet_history`. Returns count."""
    from db_manager import supabase

    records = parse_transactions_csv(path)
    if not supabase:
        print("[bet_history] Supabase unavailable; skipping upload.")
        return 0

    payloads = [
        {key: value for key, value in record.items() if key != "tags"} | {"tags": record["tags"]}
        for record in records
    ]
    inserted = 0
    for start in range(0, len(payloads), batch_size):
        chunk = payloads[start : start + batch_size]
        try:
            supabase.table("bet_history").upsert(chunk, on_conflict="bet_id").execute()
            inserted += len(chunk)
        except Exception as exc:  # noqa: BLE001 - best-effort import
            print(f"[bet_history] batch insert failed at {start}: {exc}")
    return inserted


def main() -> None:
    path = BET_HISTORY_CSV
    if not os.path.exists(path):
        print(f"[bet_history] CSV not found at {path}. Set BET_HISTORY_CSV.")
        return
    records = parse_transactions_csv(path)
    summary = summarize(records)
    write_summary(summary)
    overall = summary["overall"]
    print(
        f"[bet_history] parsed {len(records)} rows, {overall['n']} settled, "
        f"ROI={overall['roi'] * 100:.2f}% -> {BET_HISTORY_SUMMARY_PATH}"
    )
    if os.getenv("BET_HISTORY_UPLOAD", "").strip().lower() in {"1", "true", "yes", "on"}:
        count = load_csv_to_supabase(path)
        print(f"[bet_history] uploaded {count} rows to Supabase.")
        if save_summary_to_supabase(summary):
            print("[bet_history] uploaded calibration summary to Supabase.")


if __name__ == "__main__":
    main()
