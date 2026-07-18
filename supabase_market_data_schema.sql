create extension if not exists pgcrypto;

create table if not exists fixtures (
    id text primary key,
    sport_key text not null,
    commence_time timestamptz,
    home_team text,
    away_team text,
    status text default 'scheduled',
    raw_event jsonb default '{}'::jsonb,
    updated_at timestamptz default now()
);

create table if not exists historical_odds (
    id bigserial primary key,
    fixture_id text not null references fixtures(id) on delete cascade,
    sport_key text not null,
    bookmaker_key text not null,
    bookmaker_title text,
    market_key text not null,
    outcome_name text not null,
    outcome_description text,
    point numeric,
    price_decimal numeric not null,
    line_hash text not null unique,
    last_update timestamptz,
    captured_at timestamptz default now(),
    raw_outcome jsonb default '{}'::jsonb
);

-- Backfill columns on existing deployments (safe to re-run).
alter table historical_odds add column if not exists outcome_description text;
alter table historical_odds add column if not exists last_update timestamptz;

create table if not exists syndicate_bets (
    id uuid primary key default gen_random_uuid(),
    fixture_id text references fixtures(id),
    sport_key text,
    market_key text,
    selection text not null,
    bookmaker_key text,
    price_decimal numeric,
    fair_price_decimal numeric,
    edge_pct numeric,
    stake_units numeric,
    status text default 'candidate',
    source text default 'research',
    metadata jsonb default '{}'::jsonb,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists odds_ingest_runs (
    id uuid primary key default gen_random_uuid(),
    status text not null,
    sports_requested text[] default '{}',
    fixtures_upserted integer default 0,
    odds_rows_upserted integer default 0,
    credits_used integer default 0,
    credits_remaining integer,
    api_requests integer default 0,
    rotation_slot integer,
    error text,
    started_at timestamptz default now(),
    finished_at timestamptz
);

-- Backfill columns on existing deployments (safe to re-run).
alter table odds_ingest_runs add column if not exists credits_used integer default 0;
alter table odds_ingest_runs add column if not exists credits_remaining integer;
alter table odds_ingest_runs add column if not exists api_requests integer default 0;
alter table odds_ingest_runs add column if not exists rotation_slot integer;

create index if not exists idx_fixtures_sport_time on fixtures(sport_key, commence_time);
create index if not exists idx_historical_odds_fixture_market on historical_odds(fixture_id, market_key);
create index if not exists idx_historical_odds_book_time on historical_odds(bookmaker_key, captured_at desc);
create index if not exists idx_historical_odds_captured_at on historical_odds(captured_at desc);
create index if not exists idx_syndicate_bets_created_at on syndicate_bets(created_at desc);
create index if not exists idx_syndicate_bets_status on syndicate_bets(status);

alter table fixtures replica identity full;
alter table historical_odds replica identity full;

do $$
begin
    alter publication supabase_realtime add table fixtures;
exception
    when duplicate_object then null;
    when undefined_object then null;
end $$;

do $$
begin
    alter publication supabase_realtime add table historical_odds;
exception
    when duplicate_object then null;
    when undefined_object then null;
end $$;

