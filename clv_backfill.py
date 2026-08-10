"""One-off repair and backfill for CLV columns in ``bets_log``.

``clv_tracker`` only looks at ungraded bets from the last ``CLV_LOOKBACK_DAYS``
days, so any bet it misses on its one pass — because the close was never posted,
or the pipeline did not run before the grader did — never gets a CLV number at
all. This walks the whole table instead and fixes two distinct problems:

1. **Transposed columns.** ``update_bet_clv`` takes ``(bet_id, closing_odds,
   clv_pct, closing_line)`` but was called positionally with the decimal price
   and the CLV percent the other way round, so ``clv_edge_pct`` holds a decimal
   price and ``closing_line_decimal`` holds a percentage. Affected rows are
   identified exactly rather than guessed: the untouched ``closing_line_american``
   is converted back to decimal, and if it matches the value sitting in
   ``clv_edge_pct`` the row is transposed and the two values are swapped back.

2. **Missing closes.** Bets with no ``clv_edge_pct`` are re-resolved against the
   Supabase ``historical_odds`` snapshots via ``clv_tracker.resolve_closing_price``.

Dry-run by default; pass ``--apply`` to write.
"""

import argparse
from collections import Counter

from clv_tracker import _fetch_historical_game_data, resolve_closing_price
from db_manager import get_all_bets, update_bet_clv
from utils.odds import american_to_decimal, decimal_to_american, parse_float

# closing_line_american -> decimal loses precision to rounding, so an exact
# equality test would miss transposed rows.
TRANSPOSE_TOLERANCE = 0.02


def detect_transposed(bet: dict) -> bool:
    """True when ``clv_edge_pct`` is holding a decimal price."""
    stored_pct = parse_float(bet.get("clv_edge_pct"))
    stored_decimal = parse_float(bet.get("closing_line_decimal"))
    american = bet.get("closing_line_american")
    if stored_pct is None or stored_decimal is None or american in (None, ""):
        return False
    try:
        true_decimal = american_to_decimal(american)
    except (TypeError, ValueError):
        return False
    if not true_decimal:
        return False
    # A real CLV percent landing within 0.02 of the closing decimal price is
    # possible in principle, so require the other column to look like a percent.
    return abs(stored_pct - true_decimal) <= TRANSPOSE_TOLERANCE and abs(stored_decimal - true_decimal) > TRANSPOSE_TOLERANCE


def placed_decimal(bet: dict):
    parsed = parse_float(bet.get("odds_decimal"))
    if parsed:
        return parsed
    try:
        return american_to_decimal(bet.get("odds", 0))
    except (TypeError, ValueError):
        return None


def clv_percent(placed: float, closing: float) -> float:
    return round(((placed / closing) - 1.0) * 100.0, 4)


def repair_transposed(bets: list, apply: bool, stats: Counter) -> None:
    for bet in bets:
        if not detect_transposed(bet):
            continue
        stats["transposed"] += 1
        true_decimal = parse_float(bet.get("clv_edge_pct"))
        true_pct = parse_float(bet.get("closing_line_decimal"))
        print(
            f"  [transposed] bet {bet.get('id')} {bet.get('selection')}: "
            f"close={true_decimal} clv={true_pct:+.2f}%"
        )
        if apply:
            update_bet_clv(
                bet["id"],
                closing_odds=bet.get("closing_line_american"),
                clv_pct=true_pct,
                closing_line=true_decimal,
            )
            stats["transposed_written"] += 1


def backfill_missing(bets: list, apply: bool, stats: Counter) -> None:
    history_cache: dict = {}
    for bet in bets:
        if parse_float(bet.get("clv_edge_pct")) is not None:
            continue
        stats["missing"] += 1

        event_id = str(bet.get("event_id") or "")
        if not event_id:
            stats["missing_no_event_id"] += 1
            continue

        if event_id not in history_cache:
            history_cache[event_id] = _fetch_historical_game_data(event_id)
        game_data = history_cache[event_id]
        if not game_data:
            stats["missing_no_history"] += 1
            continue

        closing, source = resolve_closing_price(game_data, bet)
        placed = placed_decimal(bet)
        if not closing or closing <= 1.0 or not placed:
            stats["missing_unresolved"] += 1
            continue

        pct = clv_percent(placed, closing)
        print(f"  [backfill] bet {bet.get('id')} {bet.get('selection')}: {pct:+.2f}% via {source}")
        if apply:
            update_bet_clv(
                bet["id"],
                closing_odds=decimal_to_american(closing),
                clv_pct=pct,
                closing_line=closing,
            )
        stats["backfilled"] += 1


def run_backfill(apply: bool = False) -> Counter:
    bets = get_all_bets()
    stats: Counter = Counter({"total": len(bets)})
    if not bets:
        print("CLV backfill: no bets found.")
        return stats

    print(f"CLV backfill over {len(bets)} bets ({'APPLY' if apply else 'DRY RUN'})...")
    repair_transposed(bets, apply, stats)
    backfill_missing(bets, apply, stats)

    tracked = sum(1 for bet in bets if parse_float(bet.get("clv_edge_pct")) is not None)
    coverage = (tracked + stats["backfilled"]) / len(bets) * 100.0
    print(
        f"CLV backfill summary: transposed={stats['transposed']} "
        f"missing={stats['missing']} backfilled={stats['backfilled']} "
        f"no_history={stats['missing_no_history']} unresolved={stats['missing_unresolved']} "
        f"projected_coverage={coverage:.1f}%"
    )
    if not apply:
        print("Dry run only — re-run with --apply to write these changes.")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    run_backfill(apply=parser.parse_args().apply)
