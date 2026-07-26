"""Entry-point for the daily Contextual Stat Enrichment ingestion job.

Fetches the previous day's box scores from free stat libraries and upserts them
into the Supabase historical-stats tables via db_manager. Runs once daily from
.github/workflows/daily_stat_ingest.yml — never during the live odds scan.
"""

from services.stat_ingest import ingest_all


def main() -> None:
    results = ingest_all()
    total = sum(results.values())
    print(f"[daily_stat_ingest] complete | {total} total rows | {results}")


if __name__ == "__main__":
    main()
