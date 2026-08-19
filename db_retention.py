"""Entry point for the scheduled Supabase retention prune.

Reads and deletes only in Supabase; costs no API credits. See
``supabase_maintenance.sql`` for the one-time reclaim that actually returns disk
after a large prune.
"""

import sys

from services.db_retention import prune_stale_rows


def main() -> None:
    result = prune_stale_rows()
    print(result["detail"], flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - report and fail the job loudly
        print(f"Retention prune failed: {exc}", flush=True)
        sys.exit(1)
