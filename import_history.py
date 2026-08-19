import os
import re
import sys
from collections import Counter

import pandas as pd

from db_manager import supabase
from utils.book_names import normalize_book
from utils.results import LOSS, PUSH, WIN, book_from_notes, normalize_result

_HISTORICAL_NOTE_RE = re.compile(r"^Historical import - ID:\s*(?P<bet_id>[^;]+?)\s*(?:;|$)")

# The readers grade on WIN/LOSS/PUSH; writing anything else here is what hid the
# imported record from every report.
_STATUS_MAP = {
    "SETTLED_WIN": WIN,
    "SETTLED_LOSS": LOSS,
    "SETTLED_PUSH": PUSH,
    "SETTLED_VOID": PUSH,
}


def _historical_notes(bet_id: str, sportsbook: object) -> str:
    """Notes carrying both the dedupe id and the book the per-book records need."""
    book = normalize_book(str(sportsbook or ""))
    return f"Historical import - ID: {bet_id};book={book};book_key={book}"


def _edge_pct(value: object) -> float:
    """The export stores EV as a fraction (0.0198); the reports read percent."""
    if not pd.notna(value):
        return 0.0
    edge = float(value)
    return edge * 100.0 if abs(edge) <= 1.0 else edge


def decimal_to_american(decimal_odds):
    if pd.isna(decimal_odds) or decimal_odds <= 1.0:
        return "-110"
    if decimal_odds >= 2.0:
        return f"+{int(round((decimal_odds - 1.0) * 100))}"
    else:
        return f"{int(round(-100 / (decimal_odds - 1.0)))}"

def import_csv():
    if not supabase:
        print("[import] Supabase client not configured.")
        return

    # Robust multi-location path resolution for bet_history.csv
    possible_paths = [
        "bet_history.csv",
        os.path.join(os.getcwd(), "bet_history.csv"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bet_history.csv"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bet_history.csv")
    ]
    
    csv_path = None
    for path in possible_paths:
        if os.path.exists(path):
            csv_path = path
            break
            
    if not csv_path:
        print(f"Error: bet_history.csv could not be found in any search path: {possible_paths}")
        return
        
    print(f"Reading bet_history.csv from: {csv_path}")
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"Error reading CSV at {csv_path}: {e}")
        return

    settled = df[df['status'].isin(list(_STATUS_MAP))].copy()
    cashed_out = sum(1 for status in df['status'] if str(status) == 'SETTLED_CASH_OUT')
    if cashed_out:
        # A cash-out settles for a partial amount that no W/L/P label describes,
        # so it stays out of the record rather than distorting ROI.
        print(f"[import] skipping {cashed_out} cashed-out wager(s): no win/loss outcome to record.")

    existing_history_ids = set()
    try:
        # Unpaged, this returned PostgREST's 1,000-row maximum, so all but the
        # first thousand ids looked absent and every run re-inserted the CSV.
        for row in _iter_history_pages("id,notes"):
            note = str(row.get("notes") or "").strip()
            match = _HISTORICAL_NOTE_RE.match(note)
            if match:
                existing_history_ids.add(match.group("bet_id"))
        if existing_history_ids:
            print(f"[import] Detected {len(existing_history_ids)} previously imported historical bet(s).")
    except Exception as e:
        print(f"[import] Could not verify existing history, proceeding cautiously: {e}")

    rows_to_insert = []
    for _, row in settled.iterrows():
        bet_id = str(row.get('bet_id') or "").strip()
        if bet_id and bet_id in existing_history_ids:
            continue

        raw_amount = float(row['amount']) if pd.notna(row['amount']) else 0.0
        payload = {
            "matchup": "Historical Wager",
            "selection": str(row['bet_info'])[:200] if pd.notna(row['bet_info']) else f"Wager ({row['bet_id']})",
            "market": str(row['type']).upper() if pd.notna(row['type']) else "UNKNOWN",
            "odds": decimal_to_american(row['odds']),
            "edge": _edge_pct(row['ev']),
            "units": round(raw_amount / 3.0, 2),
            "sport": str(row['sports']).lower() if pd.notna(row['sports']) else "unknown",
            "result": _STATUS_MAP[row['status']],
            "date": row['time_placed_iso'],
            "graded_at": row['time_settled_iso'],
            "notes": _historical_notes(bet_id or str(row['bet_id']), row.get('sportsbook')),
        }
        if pd.notna(row.get('closing_line')) and float(row['closing_line']) > 1.0:
            payload["closing_line_decimal"] = float(row['closing_line'])
        rows_to_insert.append(payload)
        
    print(f"Found {len(rows_to_insert)} settled bets to import.")

    chunk_size = 1000
    for i in range(0, len(rows_to_insert), chunk_size):
        chunk = rows_to_insert[i:i + chunk_size]
        try:
            supabase.table("bets_log").insert(chunk).execute()
            print(f"Inserted {min(i + chunk_size, len(rows_to_insert))} / {len(rows_to_insert)} rows...")
        except Exception as e:
            print(f"Failed to insert chunk starting at {i}: {e}")
            
    print("Historical import complete! Monte Carlo is ready.")


PAGE_SIZE = 1000
WRITE_CHUNK = 500


def _iter_history_pages(columns: str = "*"):
    """Every previously imported row, paged past PostgREST's row ceiling.

    Ordered by id so a page's contents do not shift while rows are rewritten.
    """
    offset = 0
    while True:
        page = (
            supabase.table("bets_log")
            .select(columns)
            .ilike("notes", "Historical import - ID:%")
            .order("id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        yield from page
        if len(page) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


def _write_repairs(payloads: list) -> int:
    """Upsert whole rows, chunked.

    PostgREST compiles an upsert to ``INSERT ... ON CONFLICT``, so Postgres checks
    NOT NULL columns against the payload before it resolves to an update: a
    three-column patch failed every chunk on ``date``. Each payload therefore
    carries the row as it was read, with only the repaired fields replaced.
    """
    written = 0
    for i in range(0, len(payloads), WRITE_CHUNK):
        chunk = payloads[i:i + WRITE_CHUNK]
        try:
            supabase.table("bets_log").upsert(chunk).execute()
            written += len(chunk)
        except Exception as e:
            print(f"[repair] failed to update chunk of {len(chunk)} row(s): {e}")
    return written


def _delete_rows(row_ids: list) -> int:
    deleted = 0
    for i in range(0, len(row_ids), WRITE_CHUNK):
        chunk = row_ids[i:i + WRITE_CHUNK]
        try:
            supabase.table("bets_log").delete().in_("id", chunk).execute()
            deleted += len(chunk)
        except Exception as e:
            print(f"[repair] failed to delete chunk of {len(chunk)} row(s): {e}")
    return deleted


def repair_history(
    csv_path: str = "bet_history.csv",
    dry_run: bool = False,
    dedupe: bool = True,
) -> int:
    """Rewrite already-imported rows with canonical results and their book.

    The first import wrote lower-case results and no sportsbook, which is why the
    overall record read empty and the per-book breakdown had nothing to group on.
    This backfills both in place instead of re-importing 22k duplicates, and drops
    the copies the unpaged dedupe check let earlier imports insert.
    """
    if not supabase:
        print("[repair] Supabase client not configured.")
        return 0
    if not os.path.exists(csv_path):
        print(f"[repair] {csv_path} not found; cannot recover the book per bet.")
        return 0

    csv_rows = pd.read_csv(csv_path)
    by_bet_id = {
        str(row["bet_id"]).strip(): row
        for _, row in csv_rows.iterrows()
        if pd.notna(row.get("bet_id"))
    }

    books: Counter = Counter()
    results: Counter = Counter()
    seen_bet_ids: set = set()
    duplicate_ids: list = []
    scanned = 0
    repaired = 0
    batch: list = []

    for row in _iter_history_pages():
        match = _HISTORICAL_NOTE_RE.match(str(row.get("notes") or "").strip())
        if not match:
            continue
        scanned += 1
        bet_id = match.group("bet_id")
        if bet_id in seen_bet_ids:
            duplicate_ids.append(row.get("id"))
            continue
        seen_bet_ids.add(bet_id)

        source = by_bet_id.get(bet_id)
        sportsbook = source.get("sportsbook") if source is not None else None
        payload = dict(row)
        payload["notes"] = _historical_notes(bet_id, sportsbook)
        status = str(source.get("status")) if source is not None else ""
        canonical = _STATUS_MAP.get(status) or normalize_result(row.get("result"))
        if canonical:
            payload["result"] = canonical
        closing = source.get("closing_line") if source is not None else None
        if pd.notna(closing) and float(closing) > 1.0:
            payload["closing_line_decimal"] = float(closing)

        books[book_from_notes(payload["notes"])] += 1
        results[payload.get("result") or ""] += 1
        batch.append(payload)
        # Written a page at a time; holding 200k full rows to write at the end is
        # what makes a job like this run out of memory or time.
        if len(batch) >= PAGE_SIZE and not dry_run:
            repaired += _write_repairs(batch)
            print(f"[repair] {repaired} row(s) rewritten...")
            batch = []

    if batch and not dry_run:
        repaired += _write_repairs(batch)

    print(
        f"[repair] scanned {scanned} imported row(s) | {len(seen_bet_ids)} distinct bet(s) | "
        f"{len(duplicate_ids)} duplicate row(s) | results {dict(results)} | "
        f"books {dict(books.most_common(10))}"
    )
    if dry_run:
        print("[repair] dry run: nothing written.")
        return 0

    print(f"[repair] {repaired} row(s) rewritten.")
    if duplicate_ids and dedupe:
        deleted = _delete_rows(duplicate_ids)
        print(f"[repair] deleted {deleted} duplicate row(s), keeping the earliest of each bet.")
    elif duplicate_ids:
        print("[repair] duplicates left in place (dedupe disabled); the record still counts them.")
    return repaired


if __name__ == "__main__":
    if "--repair" in sys.argv:
        repair_history(
            dry_run="--dry-run" in sys.argv,
            dedupe="--no-dedupe" not in sys.argv,
        )
    else:
        import_csv()
