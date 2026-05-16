# Bee-Baked Syndicate Schema Reference

## Market Cache Tables

### fixtures

- `id text primary key`: sportsbook/API event id.
- `sport_key text`: source sport key such as `basketball_nba`.
- `commence_time timestamptz`
- `home_team text`
- `away_team text`
- `status text`
- `raw_event jsonb`
- `updated_at timestamptz`

### historical_odds

- `id bigserial primary key`
- `fixture_id text references fixtures(id)`
- `sport_key text`
- `bookmaker_key text`
- `bookmaker_title text`
- `market_key text`
- `outcome_name text`
- `point numeric`
- `price_decimal numeric`
- `line_hash text unique`
- `captured_at timestamptz`
- `raw_outcome jsonb`

### syndicate_bets

- `id uuid primary key`
- `fixture_id text`
- `sport_key text`
- `market_key text`
- `selection text`
- `bookmaker_key text`
- `price_decimal numeric`
- `fair_price_decimal numeric`
- `edge_pct numeric`
- `stake_units numeric`
- `status text`
- `source text`
- `metadata jsonb`
- `created_at timestamptz`

## Existing Operational Tables

- `bets_log`: legacy/current bet ledger used by Python alerting and grading.
- `execution_orders`, `execution_child_orders`, `execution_fills`, `venue_metrics`: execution desk tables.
- `alerts_sent`: alert dedupe/audit table.
- `workflow_runs`: pipeline run summaries.

