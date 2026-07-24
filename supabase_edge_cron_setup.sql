-- Supabase-side scheduler for the odds-cache-ingest Edge Function.
-- Replace the project ref and function secret before running.
--
-- Cadence is concentrated on the hours US sharp/rec markets are actually up and
-- on the sports the syndicate is historically best at (MLB, then WNBA in-season;
-- add NBA/NHL when their seasons resume). Ingestion is PAUSED overnight
-- (05:00-10:59 UTC = ~12am-6am Central) since no bets are placed then, EXCEPT
-- one "opener" pull at 09:30 UTC (~4:30am CT) to capture the freshest, stalest
-- opening lines for the pre-market opener scan to review.
--
--   Prime game hours  16:00-04:59 UTC  (~11am-midnight CT)  -> every 30 min
--   Morning pregame   11:00-15:59 UTC  (~6am-11am CT)       -> every 60 min
--   Overnight opener   09:30 UTC       (~4:30am CT)          -> one run
--   Overnight/blackout 05:00-09:29 UTC (~12am-4:30am CT)     -> no runs
--
-- Budget: ~32 runs/day. Player props need BOTH a sharp (Pinnacle/EU) and a soft
-- (US) per-event pull, budgeted as an atomic pair, so ODDS_MAX_CREDITS_PER_RUN
-- must clear ~22 (2 sports main = 12 + one prop pair = 10). At 24 credits/run
-- this lands ~20k credits/month. The old 14-credit ceiling could only fund the
-- sharp half of a prop, so soft-book props never landed and no prop could alert.
-- The Edge Function enforces the per-run ceiling and rotates the expensive
-- derivative/alternate/player-prop pairs across cycles.

create extension if not exists pg_net;
create extension if not exists pg_cron;

-- Store secrets with Supabase Vault or replace these placeholders in a private SQL session.
-- Recommended secrets:
--   ODDS_INGEST_FUNCTION_URL = https://<project-ref>.supabase.co/functions/v1/odds-cache-ingest
--   ODDS_INGEST_FUNCTION_SECRET = <your ODDS_INGEST_FUNCTION_SECRET, kept out of git>

-- Remove any prior schedules so we do not double-spend credits.
select cron.unschedule(jobname)
from cron.job
where jobname in (
    'odds-cache-ingest-every-5-minutes',
    'odds-cache-ingest-every-10-minutes',
    'odds-cache-ingest-prime',
    'odds-cache-ingest-morning',
    'odds-cache-ingest-opener'
);

-- Prime game hours: every 30 minutes, 16:00-04:59 UTC (~11am-midnight Central).
select cron.schedule(
    'odds-cache-ingest-prime',
    '*/30 16-23,0-4 * * *',
    $$
    select net.http_post(
        url := 'https://<project-ref>.supabase.co/functions/v1/odds-cache-ingest',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'x-ingest-secret', '<replace-with-ODDS_INGEST_FUNCTION_SECRET>'
        ),
        body := jsonb_build_object('trigger', 'pg_cron')
    );
    $$
);

-- Morning pregame: every 60 minutes, 11:00-15:59 UTC (~6am-11am Central).
select cron.schedule(
    'odds-cache-ingest-morning',
    '0 11-15 * * *',
    $$
    select net.http_post(
        url := 'https://<project-ref>.supabase.co/functions/v1/odds-cache-ingest',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'x-ingest-secret', '<replace-with-ODDS_INGEST_FUNCTION_SECRET>'
        ),
        body := jsonb_build_object('trigger', 'pg_cron')
    );
    $$
);

-- Overnight opener: one run at 09:30 UTC (~4:30am Central). Captures the
-- freshest opening lines so the pre-market opener scan has data to review.
select cron.schedule(
    'odds-cache-ingest-opener',
    '30 9 * * *',
    $$
    select net.http_post(
        url := 'https://<project-ref>.supabase.co/functions/v1/odds-cache-ingest',
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'x-ingest-secret', '<replace-with-ODDS_INGEST_FUNCTION_SECRET>'
        ),
        body := jsonb_build_object('trigger', 'pg_cron_opener')
    );
    $$
);

-- Confirm they registered:
select jobname, schedule, active from cron.job
where jobname in (
    'odds-cache-ingest-prime',
    'odds-cache-ingest-morning',
    'odds-cache-ingest-opener'
);
