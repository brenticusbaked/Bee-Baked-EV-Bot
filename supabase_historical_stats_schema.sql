-- Historical box-score / player-log cache for the Contextual Stat Enrichment
-- Engine. Populated once daily by the daily_stat_ingest workflow using free
-- Python stat libraries, then read (never written) by the live odds scan to
-- append "Last 10" hit rates to Discord slips. No live odds credits are spent
-- here. Safe to re-run: every statement is idempotent.

create extension if not exists pgcrypto;

-- Shared column contract for every player-log table:
--   game_date    the calendar date of the game (yesterday's slate at ingest)
--   league       the Odds API sport key (e.g. baseball_mlb, basketball_wnba)
--   player_name  normalized display name used to join against prop selections
--   team/opponent context for display
--   dedicated numeric columns for the core prop metrics of that sport
--   stats jsonb  full stat line so new prop types work without a migration
-- Uniqueness is (player_name, game_date, league) so re-ingesting a day upserts.

create table if not exists mlb_player_logs (
    id bigserial primary key,
    game_date date not null,
    league text not null default 'baseball_mlb',
    player_id text,
    player_name text not null,
    team text,
    opponent text,
    -- batter metrics
    hits numeric,
    total_bases numeric,
    runs numeric,
    rbis numeric,
    home_runs numeric,
    stolen_bases numeric,
    walks numeric,
    -- pitcher metrics
    strikeouts numeric,
    outs numeric,
    earned_runs numeric,
    hits_allowed numeric,
    walks_allowed numeric,
    stats jsonb default '{}'::jsonb,
    created_at timestamptz default now(),
    unique (player_name, game_date, league)
);

create table if not exists nba_player_logs (
    id bigserial primary key,
    game_date date not null,
    league text not null default 'basketball_nba',
    player_id text,
    player_name text not null,
    team text,
    opponent text,
    points numeric,
    rebounds numeric,
    assists numeric,
    threes_made numeric,
    steals numeric,
    blocks numeric,
    turnovers numeric,
    minutes numeric,
    stats jsonb default '{}'::jsonb,
    created_at timestamptz default now(),
    unique (player_name, game_date, league)
);

create table if not exists wnba_player_logs (
    id bigserial primary key,
    game_date date not null,
    league text not null default 'basketball_wnba',
    player_id text,
    player_name text not null,
    team text,
    opponent text,
    points numeric,
    rebounds numeric,
    assists numeric,
    threes_made numeric,
    steals numeric,
    blocks numeric,
    turnovers numeric,
    minutes numeric,
    stats jsonb default '{}'::jsonb,
    created_at timestamptz default now(),
    unique (player_name, game_date, league)
);

create table if not exists nfl_player_logs (
    id bigserial primary key,
    game_date date not null,
    league text not null default 'americanfootball_nfl',
    player_id text,
    player_name text not null,
    team text,
    opponent text,
    passing_yards numeric,
    passing_tds numeric,
    rushing_yards numeric,
    rushing_tds numeric,
    receiving_yards numeric,
    receptions numeric,
    receiving_tds numeric,
    total_tds numeric,
    interceptions numeric,
    stats jsonb default '{}'::jsonb,
    created_at timestamptz default now(),
    unique (player_name, game_date, league)
);

create table if not exists soccer_player_logs (
    id bigserial primary key,
    game_date date not null,
    league text not null,
    player_id text,
    player_name text not null,
    team text,
    opponent text,
    goals numeric,
    assists numeric,
    shots numeric,
    shots_on_target numeric,
    passes numeric,
    tackles numeric,
    saves numeric,
    minutes numeric,
    stats jsonb default '{}'::jsonb,
    created_at timestamptz default now(),
    unique (player_name, game_date, league)
);

-- Tennis is match-level (no per-player box score); one row per player per match.
create table if not exists tennis_match_logs (
    id bigserial primary key,
    game_date date not null,
    league text not null,
    tour text,
    player_name text not null,
    opponent text,
    tournament text,
    surface text,
    result text,
    sets_won numeric,
    games_won numeric,
    aces numeric,
    double_faults numeric,
    first_serve_pct numeric,
    break_points_saved numeric,
    stats jsonb default '{}'::jsonb,
    created_at timestamptz default now(),
    unique (player_name, game_date, league)
);

create index if not exists idx_mlb_logs_player_date on mlb_player_logs(player_name, game_date desc);
create index if not exists idx_nba_logs_player_date on nba_player_logs(player_name, game_date desc);
create index if not exists idx_wnba_logs_player_date on wnba_player_logs(player_name, game_date desc);
create index if not exists idx_nfl_logs_player_date on nfl_player_logs(player_name, game_date desc);
create index if not exists idx_soccer_logs_player_date on soccer_player_logs(player_name, game_date desc);
create index if not exists idx_tennis_logs_player_date on tennis_match_logs(player_name, game_date desc);
