---
name: syndicate-quant-analysis
description: Analyze Bee-Baked betting syndicate data, Supabase market caches, CLV, venue metrics, model performance, exposure, and backtesting workflows. Use when Codex needs to inspect syndicate tables, design or review betting strategies, propose threshold changes, write backtests, or prepare data-driven pull requests.
---

# Syndicate Quant Analysis

Use this skill for quantitative work on the Bee-Baked betting syndicate.

## Workflow

1. Start from cached Supabase tables, not live sportsbook/API calls.
2. Inspect schema assumptions before writing analysis code.
3. Separate production alerts from research/backtests.
4. Prefer CLV, edge capture, fill rate, and sample-size-aware metrics over win/loss alone.
5. Keep strategy changes measurable: include the before/after metric, sample window, and rollback condition.

## Data Sources

Prefer these tables when available:

- `fixtures`: canonical event schedule and team metadata.
- `historical_odds`: bookmaker market snapshots captured by Supabase ingestion.
- `syndicate_bets` or `bets_log`: placed/recommended bets.
- `execution_orders`, `execution_child_orders`, `execution_fills`, `venue_metrics`: paper/live execution quality.
- `workflow_runs`, `alerts_sent`: operational health.

If a table is missing, generate a migration or fallback query. Do not consume Odds API credits for exploratory analysis.

## Backtest Pattern

For any strategy:

1. Define the signal using only data available before the bet timestamp.
2. Compute the offered line, fair line, edge, and book.
3. Join later CLV and grading data when available.
4. Report count, average edge, CLV beaten rate, average CLV, hit rate, units, max drawdown, and book/source split.
5. Flag low-sample results instead of overfitting.

## PR Expectations

When proposing code changes:

- Keep changes behind feature flags when behavior is experimental.
- Add tests for deterministic parsing, scoring, and filtering logic.
- Include a short rollout plan and rollback condition.
- Avoid adding new direct API fetches unless the cache path is unavailable.

