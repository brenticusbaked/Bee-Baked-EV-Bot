"""Entry-point for the daily Contextual Stat Enrichment ingestion job.

Fetches the previous day's box scores from free stat libraries and upserts them
into the Supabase historical-stats tables via db_manager. Runs once daily from
.github/workflows/daily_stat_ingest.yml — never during the live odds scan.
"""

from datetime import datetime, timezone

from services.stat_ingest import _yesterday, ingest_all


def main() -> None:
    now = datetime.now(timezone.utc)
    game_date = now.date() if now.hour >= 20 else _yesterday()
    results = ingest_all(game_date)
    total = sum(results.values())
    print(f"[daily_stat_ingest] complete | {total} total rows | {results}")


if __name__ == "__main__":
    main()
