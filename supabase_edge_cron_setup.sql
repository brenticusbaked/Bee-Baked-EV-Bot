-- Supabase-side scheduler for the odds-cache-ingest Edge Function.
-- Replace the project ref and function secret before running.
--
-- Cadence is concentrated on the hours US sharp/rec markets are actually up and
-- on the sports the syndicate is historically best at (MLB, then WNBA in-season;
-- add NBA/NHL when their seasons resume). Ingestion is PAUSED overnight
-- (05:00-10:59 UTC = ~12am-6am Central) since no bets are placed then.
--
--   Prime game hours  16:00-04:59 UTC  (~11am-midnight CT)  -> every 20 min
--   Morning pregame   11:00-15:59 UTC  (~6am-11am CT)       -> every 30 min
--   Overnight/blackout 05:00-10:59 UTC (~12am-6am CT)        -> no runs
--
-- Budget: ~49 runs/day. With ODDS_MAX_CREDITS_PER_RUN=14 and two in-season
-- sports (main = 3 markets x us,eu = 6 credits each), this lands ~19-20k
-- credits/month, leaving headroom for rotating alternates/player-prop pulls.
-- The Edge Function enforces the per-run ceiling and rotates the expensive
-- derivative/alternate/player-prop markets across cycles.

create extension if not exists pg_net;
create extension if not exists pg_cron;

-- Store secrets with Supabase Vault or replace these placeholders in a private SQL session.
-- Recommended secrets:
--   ODDS_INGEST_FUNCTION_URL = https://<project-ref>.functions.supabase.co/odds-cache-ingest
--   ODDS_INGEST_FUNCTION_SECRET = <your ODDS_INGEST_FUNCTION_SECRET, kept out of git>

-- Remove any prior schedules so we do not double-spend credits.
select cron.unschedule(jobname)
from cron.job
where jobname in (
    'odds-cache-ingest-every-5-minutes',
    'odds-cache-ingest-every-10-minutes',
    'odds-cache-ingest-prime',
    'odds-cache-ingest-morning'
);

-- Prime game hours: every 20 minutes, 16:00-04:59 UTC (~11am-midnight Central).
select cron.schedule(
    'odds-cache-ingest-prime',
    '*/20 16-23,0-4 * * *',
    $$
    select net.http_post(
        url := 'https://<project-ref>.functions.supabase.co/odds-cache-ingest',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'x-ingest-secret', '<replace-with-ODDS_INGEST_FUNCTION_SECRET>'
        ),
        body := jsonb_build_object('trigger', 'pg_cron')
    );
    $$
);

-- Morning pregame: every 30 minutes, 11:00-15:59 UTC (~6am-11am Central).
select cron.schedule(
    'odds-cache-ingest-morning',
    '*/30 11-15 * * *',
    $$
    select net.http_post(
        url := 'https://<project-ref>.functions.supabase.co/odds-cache-ingest',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'x-ingest-secret', '<replace-with-ODDS_INGEST_FUNCTION_SECRET>'
        ),
        body := jsonb_build_object('trigger', 'pg_cron')
    );
    $$
);

-- Confirm they registered:
select jobname, schedule, active from cron.job
where jobname in ('odds-cache-ingest-prime', 'odds-cache-ingest-morning');
