#!/usr/bin/env python3
"""Analyze /logs betting data and alert when edge exceeds a threshold."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ODDS_FIELDS = ("odds", "price", "american_odds", "decimal_odds", "book_odds")
PROB_FIELDS = ("model_probability", "fair_probability", "probability", "win_probability")
RESULT_FIELDS = ("result", "outcome", "status", "graded_result")
GROUP_FIELDS = ("sport", "market", "selection", "player", "team", "matchup", "stat", "line")
WIN_VALUES = {"win", "won", "hit", "cash", "cashed", "true", "1"}
LOSS_VALUES = {"loss", "lost", "miss", "false", "0"}
PUSH_VALUES = {"push", "void", "cancelled", "canceled", "refund", "refunded"}

# Webhook retry configuration
WEBHOOK_MAX_RETRIES = 3
WEBHOOK_RETRY_DELAY = 2  # seconds
WEBHOOK_TIMEOUT = 15


def parse_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def odds_to_decimal(value: Any) -> Optional[float]:
    number = parse_number(value)
    if number is None:
        return None
    if number > 1.0 and not str(value).strip().startswith("+"):
        return number
    if number > 0:
        return (number / 100.0) + 1.0
    if number < 0:
        return (100.0 / abs(number)) + 1.0
    return None


def implied_probability(odds: Any) -> Optional[float]:
    decimal = odds_to_decimal(odds)
    if decimal is None or decimal <= 1.0:
        return None
    return 1.0 / decimal


def normalize_probability(value: Any) -> Optional[float]:
    number = parse_number(value)
    if number is None:
        return None
    if number > 1.0:
        number = number / 100.0
    if 0.0 <= number <= 1.0:
        return number
    return None


def first_value(row: Dict[str, Any], names: Iterable[str]) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name in lowered and lowered[name] not in (None, ""):
            return lowered[name]
    return None


def normalize_result(row: Dict[str, Any]) -> Optional[str]:
    value = first_value(row, RESULT_FIELDS)
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in WIN_VALUES:
        return "win"
    if text in LOSS_VALUES:
        return "loss"
    if text in PUSH_VALUES:
        return "push"
    return None


def group_key(row: Dict[str, Any]) -> Tuple[str, ...]:
    lowered = {str(key).lower(): value for key, value in row.items()}
    values = []
    for field in GROUP_FIELDS:
        value = lowered.get(field)
        values.append(str(value).strip().lower() if value not in (None, "") else "")
    return tuple(values)


def display_label(row: Dict[str, Any]) -> str:
    parts = []
    for field in ("sport", "market", "matchup", "selection", "player", "stat", "line", "book"):
        value = first_value(row, (field,))
        if value not in (None, ""):
            parts.append(f"{field}={value}")
    return " | ".join(parts) or "unlabeled opportunity"


def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("rows", "bets", "data", "records"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        return [data]
    return []


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_logs(log_dir: Path) -> List[Dict[str, Any]]:
    if not log_dir.exists():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")
    rows: List[Dict[str, Any]] = []
    for path in sorted(log_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".csv":
            loaded = read_csv(path)
        elif suffix == ".json":
            loaded = read_json(path)
        elif suffix in {".jsonl", ".ndjson"}:
            loaded = read_jsonl(path)
        else:
            continue
        for row in loaded:
            row["_source_file"] = str(path)
            rows.append(row)
    return rows


def historical_probabilities(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, ...], Tuple[float, int]]:
    counts: Dict[Tuple[str, ...], Counter] = defaultdict(Counter)
    for row in rows:
        result = normalize_result(row)
        if result in {"win", "loss"}:
            counts[group_key(row)][result] += 1

    probabilities = {}
    for key, counter in counts.items():
        total = counter["win"] + counter["loss"]
        if total:
            probabilities[key] = (counter["win"] / total, total)
    return probabilities


def find_edges(rows: List[Dict[str, Any]], threshold: float, min_sample: int) -> List[Dict[str, Any]]:
    history = historical_probabilities(rows)
    alerts = []

    for row in rows:
        odds = first_value(row, ODDS_FIELDS)
        implied = implied_probability(odds)
        if implied is None:
            continue

        fair = normalize_probability(first_value(row, PROB_FIELDS))
        sample_size = None
        source = "explicit_probability"
        if fair is None:
            fair, sample_size = history.get(group_key(row), (None, 0))
            source = "historical_win_rate"
            if fair is None or sample_size < min_sample:
                continue

        edge = fair - implied
        if edge >= threshold:
            alerts.append(
                {
                    "label": display_label(row),
                    "edge": round(edge, 6),
                    "edge_pct": round(edge * 100.0, 2),
                    "fair_probability": round(fair, 6),
                    "implied_probability": round(implied, 6),
                    "odds": odds,
                    "probability_source": source,
                    "sample_size": sample_size,
                    "source_file": row.get("_source_file"),
                }
            )

    return sorted(alerts, key=lambda item: item["edge"], reverse=True)


def validate_webhook_url(webhook_url: Optional[str]) -> bool:
    """Validate Discord webhook URL format and presence."""
    if not webhook_url or not isinstance(webhook_url, str):
        return False
    
    valid_prefixes = (
        "https://discordapp.com/api/webhooks/",
        "https://discord.com/api/webhooks/",
    )
    
    return any(webhook_url.startswith(prefix) for prefix in valid_prefixes)


def send_discord_update(
    webhook_url: str,
    alerts: List[Dict[str, Any]],
    threshold: float,
    rows_scanned: int,
    note: Optional[str] = None,
) -> None:
    """Send Discord webhook with retry logic and better error handling."""
    
    if not validate_webhook_url(webhook_url):
        raise ValueError(f"Invalid Discord webhook URL format")
    
    if alerts:
        top_lines = [
            f"**{item['edge_pct']:.2f}% edge** | {item['label']} | odds {item['odds']} | fair {item['fair_probability']:.3f} vs implied {item['implied_probability']:.3f}"
            for item in alerts[:10]
        ]
        description = "\n".join(top_lines)[:3900]
    else:
        description = note or f"No opportunities cleared {threshold * 100:.2f}% edge across {rows_scanned} scanned row(s)."

    payload = {
        "content": f"Odds edge update: {len(alerts)} opportunity(s) at or above {threshold * 100:.2f}%",
        "embeds": [{"title": "Odds Edge Update", "description": description}],
    }
    data = json.dumps(payload).encode("utf-8")
    
    last_error = None
    
    for attempt in range(WEBHOOK_MAX_RETRIES):
        try:
            request = urllib.request.Request(
                webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            
            with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT) as response:
                if response.status >= 300:
                    error_msg = f"Discord webhook returned HTTP {response.status}"
                    
                    # Retry on server errors (5xx)
                    if 500 <= response.status < 600 and attempt < WEBHOOK_MAX_RETRIES - 1:
                        print(f"Attempt {attempt + 1}/{WEBHOOK_MAX_RETRIES}: {error_msg}. Retrying in {WEBHOOK_RETRY_DELAY}s...")
                        time.sleep(WEBHOOK_RETRY_DELAY)
                        continue
                    
                    # Don't retry on client errors (4xx) - permanent failures
                    raise RuntimeError(error_msg)
                
                print(f"Discord webhook sent successfully (attempt {attempt + 1})")
                return  # Success
                
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            last_error = exc
            status_code = None
            
            if isinstance(exc, urllib.error.HTTPError):
                status_code = exc.code
            
            error_detail = f"{type(exc).__name__}: {exc}"
            if status_code:
                error_detail = f"HTTP {status_code}: {exc.reason}"
            
            # Check if we should retry
            should_retry = False
            if isinstance(exc, urllib.error.URLError):
                should_retry = True  # Retry connection errors
            elif status_code and status_code >= 500:
                should_retry = True  # Retry server errors
            
            if should_retry and attempt < WEBHOOK_MAX_RETRIES - 1:
                print(f"Attempt {attempt + 1}/{WEBHOOK_MAX_RETRIES}: Discord webhook failed ({error_detail}). Retrying in {WEBHOOK_RETRY_DELAY}s...")
                time.sleep(WEBHOOK_RETRY_DELAY)
                continue
            
            if attempt == WEBHOOK_MAX_RETRIES - 1:
                # Final attempt failed
                raise RuntimeError(f"Discord webhook failed after {WEBHOOK_MAX_RETRIES} attempts: {error_detail}")
    
    # Should not reach here, but just in case
    if last_error:
        raise RuntimeError(f"Discord webhook failed: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", default="logs", help="Directory containing CSV, JSON, or JSONL betting logs.")
    parser.add_argument("--threshold", type=float, default=0.03, help="Minimum edge as a decimal. 0.03 means 3%%.")
    parser.add_argument("--min-sample", type=int, default=20, help="Minimum graded historical rows for empirical probability.")
    parser.add_argument("--webhook-url", default=os.getenv("DISCORD_WEBHOOK_URL"), help="Discord webhook URL. Defaults to DISCORD_WEBHOOK_URL.")
    parser.add_argument("--always-notify", action="store_true", help="Send a Discord update even when no edge clears the threshold.")
    parser.add_argument("--allow-missing-logs", action="store_true", help="Treat a missing logs directory as an empty scan instead of failing.")
    parser.add_argument("--json", action="store_true", help="Print full alert JSON.")
    args = parser.parse_args()

    note = None
    try:
        rows = load_logs(Path(args.logs))
    except FileNotFoundError as exc:
        if not args.allow_missing_logs:
            raise
        rows = []
        note = str(exc)

    alerts = find_edges(rows, threshold=args.threshold, min_sample=args.min_sample)

    if args.json or not alerts:
        print(json.dumps({"rows": len(rows), "alerts": alerts}, indent=2))
    else:
        for item in alerts:
            print(f"{item['edge_pct']:.2f}% edge - {item['label']} (odds {item['odds']})")

    if args.webhook_url and (alerts or args.always_notify):
        if not validate_webhook_url(args.webhook_url):
            print(f"ERROR: Invalid Discord webhook URL. Check DISCORD_WEBHOOK_URL secret in GitHub.", file=sys.stderr)
            return 1
        
        try:
            send_discord_update(args.webhook_url, alerts, args.threshold, len(rows), note=note)
        except Exception as exc:
            print(f"ERROR: Failed to send Discord webhook: {exc}", file=sys.stderr)
            print(f"Webhook URL format: {args.webhook_url[:50]}...", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as exc:
        print(f"odds_edge_alert failed: {exc}", file=sys.stderr)
        sys.exit(1)
