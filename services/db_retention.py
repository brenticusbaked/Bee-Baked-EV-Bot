"""Retention pruning for the Supabase project.

The database grows without bound: ``historical_odds`` keys every row on a
``line_hash`` that includes the price, so each price change is a new row forever,
and ``fixtures``/``historical_odds`` each carried a full raw JSON copy of data
that already lives in their own columns. Nothing reads those blobs.

This module deletes rows past their useful life. Note that deletes alone do not
return disk to Supabase: PostgreSQL marks the tuples dead and reuses the space
for future rows. ``supabase_maintenance.sql`` holds the one-time ``VACUUM FULL``
that actually shrinks the files.

Windows are per-table and configurable, and default to more than any consumer
needs: the grader looks back a week, ``clv_tracker`` resolves closing prices for
recent bets, and the models read the last two seasons of player logs.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import db_manager
from utils.config import env_flag

# Retention windows in days. `historical_odds` is the growth driver but also the
# CLV fallback, so it keeps the widest odds window.
DEFAULT_RETENTION_DAYS: dict[str, int] = {
    "historical_odds": 45,
    # Deleting a fixture cascades its odds rows, which is where the raw JSON
    # copies sit; nothing keys off a settled fixture after grading.
    "fixtures": 45,
    "odds_ingest_runs": 14,
    "alerts_sent": 30,
    "workflow_runs": 30,
    "venue_metrics": 90,
}
# Which timestamp column each table ages on.
AGE_COLUMN: dict[str, str] = {
    "historical_odds": "captured_at",
    "fixtures": "commence_time",
    "odds_ingest_runs": "started_at",
    "alerts_sent": "sent_at",
    "workflow_runs": "run_at",
    "venue_metrics": "measured_at",
}
# Player logs feed the last-10 and season-form features, so they age on game date
# and keep two seasons by default.
PLAYER_LOG_TABLES = (
    "mlb_player_logs",
    "nba_player_logs",
    "wnba_player_logs",
    "nfl_player_logs",
    "soccer_player_logs",
    "tennis_match_logs",
)
PLAYER_LOG_RETENTION_DAYS = 730


def retention_days(table: str, default: int) -> int:
    """Per-table override, e.g. ``RETENTION_DAYS_HISTORICAL_ODDS=30``."""
    raw = (os.getenv(f"RETENTION_DAYS_{table.upper()}") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        print(f"[retention] ignoring non-numeric RETENTION_DAYS_{table.upper()}={raw!r}", flush=True)
        return default
    return value if value > 0 else default


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _prune(table: str, column: str, days: int, dry_run: bool) -> int:
    """Delete rows in ``table`` older than ``days``; return the row count."""
    if not db_manager.supabase:
        return 0

    def count() -> int:
        res = (
            db_manager.supabase.table(table)
            .select("*", count="exact")
            .lt(column, _cutoff(days))
            .limit(1)
            .execute()
        )
        return int(getattr(res, "count", 0) or 0)

    stale = db_manager._safe_execute(count, 0)
    if not stale:
        print(f"[retention] {table}: nothing older than {days}d", flush=True)
        return 0
    if dry_run:
        print(f"[retention] {table}: would delete {stale} rows older than {days}d", flush=True)
        return 0

    def delete() -> None:
        db_manager.supabase.table(table).delete().lt(column, _cutoff(days)).execute()

    db_manager._safe_execute(delete, None)
    print(f"[retention] {table}: deleted {stale} rows older than {days}d", flush=True)
    return stale


def prune_stale_rows() -> dict[str, object]:
    """Apply every retention window. Safe to run repeatedly."""
    if not db_manager.supabase:
        return {"detail": "retention skipped: no supabase client", "count": 0, "label": "rows"}

    dry_run = env_flag("DB_RETENTION_DRY_RUN", False)
    deleted = 0
    for table, default_days in DEFAULT_RETENTION_DAYS.items():
        deleted += _prune(table, AGE_COLUMN[table], retention_days(table, default_days), dry_run)
    for table in PLAYER_LOG_TABLES:
        deleted += _prune(table, "game_date", retention_days(table, PLAYER_LOG_RETENTION_DAYS), dry_run)

    prefix = "retention dry run" if dry_run else "retention complete"
    detail = f"{prefix} | {deleted} rows {'matched' if dry_run else 'deleted'}"
    if deleted:
        detail += " | run supabase_maintenance.sql VACUUM FULL to return the disk"
    return {"detail": detail, "count": deleted, "label": "rows"}
