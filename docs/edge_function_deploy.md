# Deploying the `odds-cache-ingest` Edge Function

The Edge Function source lives in the repo at
`supabase/functions/odds-cache-ingest/index.ts`. It runs on Supabase's servers,
calls The Odds API every 10 minutes (triggered by `pg_cron`), and writes
normalized rows into `historical_odds`. Nothing about it runs from GitHub Actions
except the optional auto-deploy below.

## Prerequisites

- Supabase CLI: `npm install -g supabase` (or `brew install supabase/tap/supabase`).
- Your project ref: Supabase → **Project Settings → General → Reference ID**.

## 1. Set the function secrets (once, and whenever they change)

These are **Supabase** function secrets — separate from GitHub Actions secrets.

```bash
supabase login
supabase link --project-ref <YOUR_PROJECT_REF>

# Odds API key pool (20k primary + three 500/mo reserves)
supabase secrets set ODDS_API_KEY=<primary> ODDS_API_KEY_2=<reserve> ODDS_API_KEY_3=<reserve> ODDS_API_KEY_4=<reserve>

# Supabase service role key (Project Settings → API) + the shared ingest secret
supabase secrets set SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
supabase secrets set ODDS_INGEST_FUNCTION_SECRET=<random-shared-secret>

# Books / markets / budget (defaults are baked into the code; override to change)
supabase secrets set ODDS_API_TARGET_BOOKS=pinnacle,fanduel,draftkings,betmgm,bet365,caesars,bovada
supabase secrets set ODDS_API_EXTENDED_REGIONS=us_ex
supabase secrets set ODDS_API_EXTENDED_BOOKS=novig,kalshi,polymarket,prophetx
supabase secrets set ODDS_EXTENDED_EVERY_N_SLOTS=3
```

Verify with `supabase secrets list` (shows names + digests, never values).

## 2. Deploy the function

```bash
supabase functions deploy odds-cache-ingest --project-ref <YOUR_PROJECT_REF>
```

After deploy it is live at:

```
https://<YOUR_PROJECT_REF>.functions.supabase.co/odds-cache-ingest
```

### Automated deploy (GitHub Action)

`.github/workflows/deploy_edge_function.yml` runs the same `supabase functions
deploy` on manual dispatch and automatically when `supabase/functions/**`
changes on `main`. It needs two **GitHub** repo secrets:

- `SUPABASE_ACCESS_TOKEN` — Supabase → Account → **Access Tokens** → generate.
- `SUPABASE_PROJECT_REF` — the same project ref as above.

Add them under repo **Settings → Secrets and variables → Actions**, then trigger
the workflow from the Actions tab (or just push a change to the function).

## 3. Schedule ingestion (once per project)

Run `supabase_edge_cron_setup.sql` in the Supabase **SQL Editor** (replace the
`<project-ref>` placeholders and the ingest-secret placeholder). This enables
`pg_cron`/`pg_net`, removes any legacy schedule, and schedules the function every
10 minutes. Confirm with:

```sql
select jobname, schedule, active from cron.job where jobname = 'odds-cache-ingest-every-10-minutes';
```

## 4. Verify it's ingesting

After the next 10-minute tick, check the run log:

```sql
select * from odds_ingest_runs order by created_at desc limit 5;
```

You want `status = 'ok'` rows with `credits_used` / `credits_remaining`
populated. If `historical_odds` stays empty, re-check the function secrets and
that `supabase_market_data_schema.sql` has been applied.
