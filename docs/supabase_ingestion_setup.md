# Supabase Market Ingestion Setup

Use this path to cache market data in Supabase and avoid spending Odds API credits from local/Codex runs.

## 1. Create Tables

Run these SQL files in the Supabase SQL editor:

1. `supabase_market_data_schema.sql`
2. `supabase_bets_log_schema_patch.sql`
3. `supabase_execution_schema.sql`

`supabase_market_data_schema.sql` creates:

- `fixtures`
- `historical_odds`
- `syndicate_bets`
- `odds_ingest_runs`

It also attempts to add `fixtures` and `historical_odds` to the `supabase_realtime` publication.

## 2. Deploy Edge Function

Install and login to the Supabase CLI locally, then link the project:

```bash
supabase link --project-ref <project-ref>
supabase functions deploy odds-cache-ingest
```

Set function secrets:

```bash
supabase secrets set ODDS_API_KEY=<odds-api-key>
supabase secrets set SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
supabase secrets set ODDS_INGEST_FUNCTION_SECRET=<shared-secret>
supabase secrets set ODDS_API_ACTIVE_SPORTS=basketball_nba,basketball_wnba,baseball_mlb,icehockey_nhl
supabase secrets set ODDS_API_MARKETS=h2h,spreads,totals
supabase secrets set ODDS_API_REGIONS=us,eu
supabase secrets set ODDS_API_TARGET_BOOKS=pinnacle,fanduel,draftkings,betmgm,bet365,caesars
```

## 3. Schedule The Function

Run `supabase_edge_cron_setup.sql` after replacing:

- `<project-ref>`
- `<replace-with-ODDS_INGEST_FUNCTION_SECRET>`

The schedule is every 5 minutes. Use Supabase cron controls to pause it on non-game days or narrow `ODDS_API_ACTIVE_SPORTS`.

## 4. Realtime Subscriptions

Frontend or agent clients can subscribe to fixture and odds changes:

```ts
supabase
  .channel("fixtures-live")
  .on("postgres_changes", { event: "*", schema: "public", table: "fixtures" }, (payload) => {
    console.log("fixture changed", payload);
  })
  .subscribe();
```

Use `historical_odds` for line-movement feeds and `fixtures` for event lifecycle.

## 5. Operating Rule

Codex, local scripts, and subagents should query Supabase tables for research. Only the Edge Function should call The Odds API on a schedule.

