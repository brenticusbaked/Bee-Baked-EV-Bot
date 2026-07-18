# Personal Betting-History Calibration

This layer folds your own realized results into the decision engine **without**
touching the fair-value math. Pinnacle stays the sole fair-market baseline, and
power de-vig / Quarter-Kelly / bankroll caps are unchanged. History only tunes
execution-side weighting, the EV floor, and CLV context.

## What it does

1. **History-weighted book scores** — books you actually beat get a small weight
   bump; chronic losers get dampened (shrunk by sample size, capped ±10%).
2. **EV floor** — alerts never fire below the EV band your history shows to be
   durably profitable. `UNIFIED_EV_FLOOR` (default `0.02`) is the floor, and the
   realized ROI-by-EV-bucket calibration can only *raise* it, never lower it.
3. **CLV baseline** — the CLV tracker shows your historical average CLV for the
   book and whether the current bet beats it.

## Data flow

```
transactions.csv ──parse/normalize──▶ bet_history (Supabase table, raw rows)
                                  └──▶ bet_history_summary (aggregates)
                                            │
        runtime overlays ◀────────load_summary (local file OR Supabase)
   (book weights, EV floor, CLV baseline)
```

The raw CSV and the local `bet_history_summary.json` are **git-ignored** — they
contain sensitive personal financial data and must never be committed.

## Importing your history

1. Apply the schema once in the Supabase SQL Editor:
   run `supabase_bet_history_schema.sql`.
2. Put your export at the repo root as `bet_history.csv` (or set
   `BET_HISTORY_CSV`).
3. Build the summary and upload to Supabase:
   ```bash
   BET_HISTORY_UPLOAD=true python bet_history.py
   ```
   Without `BET_HISTORY_UPLOAD` it only writes the local `bet_history_summary.json`.

Once the summary exists (locally or in Supabase), the scanners pick it up
automatically. Re-run whenever you want to refresh the calibration.

## Tunables (env)

| Var | Default | Meaning |
|---|---|---|
| `UNIFIED_EV_FLOOR` | `0.02` | Hard minimum EV for any alert |
| `HISTORY_SHRINK_K` | `150` | Bets before a book's ROI is ~half-trusted |
| `HISTORY_MIN_BOOK_SAMPLE` | `50` | Min settled bets to weight a book |
| `HISTORY_MAX_BOOK_ADJUST` | `0.10` | Max ± weight move from realized ROI |
| `HISTORY_EV_FLOOR_MIN_ROI` | `0.0` | ROI a bucket must clear to validate |
| `HISTORY_EV_FLOOR_MIN_SAMPLE` | `200` | Min bets for a bucket to count |

## What it deliberately does NOT do

- It does not use per-bet outcomes to pick current bets (no look-ahead).
- It does not replace or adjust Pinnacle-derived fair probabilities.
- It does not change the de-vig method, Kelly fraction, or bankroll cap.
