-- Core tables used by db_manager.py that were missing CREATE TABLE statements.
-- Run this in the Supabase SQL editor BEFORE deploying the bot.
-- All tables use IF NOT EXISTS so this is safe to re-run.

-- ============================================================
-- bets_log: Primary bet tracking table
-- ============================================================
create table if not exists bets_log (
    id bigserial primary key,
    date text,
    matchup text,
    market text,
    selection text,
    odds text,
    edge text,
    units text,
    fair_price text,
    sport text,
    event_id text,
    closing_line_pinnacle text default '',
    result text default '',
    -- Extended columns (from supabase_bets_log_schema_patch.sql)
    edge_pct numeric,
    odds_decimal numeric,
    fair_price_decimal numeric,
    bet_source text,
    market_type text,
    notes text,
    closing_line_american text,
    closing_line_decimal numeric,
    clv_edge_pct numeric,
    clv_tracked_at timestamptz,
    graded_at timestamptz,
    created_at timestamptz default now()
);

create index if not exists idx_bets_log_date on bets_log(date);
create index if not exists idx_bets_log_event_id on bets_log(event_id);
create index if not exists idx_bets_log_market_type on bets_log(market_type);
create index if not exists idx_bets_log_result on bets_log(result);

-- ============================================================
-- odds_cache: Stores the master odds cache blob
-- ============================================================
create table if not exists odds_cache (
    id text primary key,
    data jsonb default '{}'::jsonb,
    updated_at timestamptz default now()
);

-- ============================================================
-- bot_state: Key-value state persistence (tracker states, etc.)
-- ============================================================
create table if not exists bot_state (
    id text primary key,
    data jsonb default '{}'::jsonb,
    updated_at timestamptz default now()
);

-- ============================================================
-- alerts_sent: Audit trail for Discord alert deduplication
-- ============================================================
create table if not exists alerts_sent (
    id bigserial primary key,
    source text,
    alert_type text,
    dedupe_key text,
    count integer default 1,
    payload_preview text,
    status text default 'sent',
    sent_at timestamptz default now()
);

create index if not exists idx_alerts_sent_dedupe on alerts_sent(dedupe_key);
create index if not exists idx_alerts_sent_sent_at on alerts_sent(sent_at desc);

-- ============================================================
-- workflow_runs: Pipeline run history for observability
-- ============================================================
create table if not exists workflow_runs (
    id bigserial primary key,
    workflow_name text,
    status text,
    runtime_seconds numeric,
    task_count integer default 0,
    failure_count integer default 0,
    alert_count integer default 0,
    graded_count integer default 0,
    tracked_count integer default 0,
    summary text,
    run_at timestamptz default now()
);

create index if not exists idx_workflow_runs_run_at on workflow_runs(run_at desc);
create index if not exists idx_workflow_runs_status on workflow_runs(status);
