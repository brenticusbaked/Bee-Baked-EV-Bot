-- Supabase-side scheduler for the odds-cache-ingest Edge Function.
-- Replace the project ref and function secret before running.
--
-- Cadence: every 10 minutes => 144 runs/day (staggered continuous ingestion).
-- Budget target: 20,000 credits/month ~= 666 credits/day ~= 4.5 credits/run.
-- The Edge Function itself enforces the per-run credit ceiling and rotates the
-- expensive derivative/alternate/player-prop markets across cycles.

create extension if not exists pg_net;
create extension if not exists pg_cron;

-- Store secrets with Supabase Vault or replace these placeholders in a private SQL session.
-- Recommended secrets:
--   ODDS_INGEST_FUNCTION_URL = https://<project-ref>.functions.supabase.co/odds-cache-ingest
--   ODDS_INGEST_FUNCTION_SECRET = shared secret checked by the Edge Function

-- Remove any legacy 5-minute schedule so we do not double-spend credits.
select cron.unschedule('odds-cache-ingest-every-5-minutes')
where exists (
    select 1 from cron.job where jobname = 'odds-cache-ingest-every-5-minutes'
);

select cron.schedule(
    'odds-cache-ingest-every-10-minutes',
    '*/10 * * * *',
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
