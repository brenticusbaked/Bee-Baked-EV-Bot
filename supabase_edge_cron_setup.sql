-- Optional Supabase-side scheduler for the odds-cache-ingest Edge Function.
-- Replace the project ref and function secret before running.

create extension if not exists pg_net;
create extension if not exists pg_cron;

-- Store secrets with Supabase Vault or replace these placeholders in a private SQL session.
-- Recommended secrets:
--   ODDS_INGEST_FUNCTION_URL = https://<project-ref>.functions.supabase.co/odds-cache-ingest
--   ODDS_INGEST_FUNCTION_SECRET = shared secret checked by the Edge Function

select cron.schedule(
    'odds-cache-ingest-every-5-minutes',
    '*/5 * * * *',
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

