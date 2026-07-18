# Supabase Market Ingestion Setup

Use this path to cache market data in Supabase and avoid spending Odds API credits from local/Codex runs.

## 1. Create Tables

Run these SQL files in the Supabase SQL editor:

1. `supabase_core_schema.sql` (bets_log, odds_cache, bot_state, alerts_sent, workflow_runs)
2. `supabase_market_data_schema.sql` (fixtures, historical_odds, syndicate_bets, odds_ingest_runs)
3. `supabase_execution_schema.sql` (execution_orders, execution_child_orders, execution_fills, venue_metrics)

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
# Primary 20k-credit key carries the budget; ODDS_API_KEY_2..4 are 500-credit
# reserves used only as failover (401/402/429), tried in fixed priority order.
supabase secrets set ODDS_API_KEY=<primary-20k-key>
supabase secrets set ODDS_API_KEY_2=<reserve-500-key>
supabase secrets set ODDS_API_KEY_3=<reserve-500-key>
supabase secrets set ODDS_API_KEY_4=<reserve-500-key>
supabase secrets set SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
supabase secrets set ODDS_INGEST_FUNCTION_SECRET=<shared-secret>
supabase secrets set ODDS_API_ACTIVE_SPORTS=basketball_nba,basketball_wnba,baseball_mlb,icehockey_nhl
supabase secrets set ODDS_API_MARKETS=h2h,spreads,totals
supabase secrets set ODDS_API_REGIONS=us,eu
# Bovada is in the `us` region, so it is covered by the main pull at no extra cost.
supabase secrets set ODDS_API_TARGET_BOOKS=pinnacle,fanduel,draftkings,betmgm,bet365,caesars,bovada
```

Extended US exchange coverage (Novig etc.) — these live in the `us_ex` region,
billed separately, so they are pulled on a slower rotation. Cost is per region,
not per book, so Kalshi/Polymarket/ProphetX ride along for free with Novig:

```bash
supabase secrets set ODDS_API_EXTENDED_REGIONS=us_ex
supabase secrets set ODDS_API_EXTENDED_BOOKS=novig,kalshi,polymarket,prophetx
# Run the extended pull every Nth 10-min slot (3 = every 30 min). 0 disables it.
supabase secrets set ODDS_EXTENDED_EVERY_N_SLOTS=3
# To also add Fliff / ESPN BET (ex-theScore), which live in the separate `us2`
# region (extra per-region credit): ODDS_API_EXTENDED_REGIONS=us_ex,us2 and add
# fliff,espnbet to ODDS_API_EXTENDED_BOOKS. theScore Bet is now ESPN BET (espnbet).
# Onyx Odds, and standalone Kalshi/Polymarket beyond us_ex, are not otherwise
# offered by The Odds API.
```

Optional budget/expansion controls (defaults shown):

```bash
# Strict per-run credit ceiling. 5 credits x 144 runs/day ~= monthly 20k budget.
supabase secrets set ODDS_MAX_CREDITS_PER_RUN=5
# Per-event enrichment (derivatives / alternates / player props) is fetched from
# the per-event odds endpoint and rotates across runs by a time-based slot.
supabase secrets set ODDS_MAX_EVENTS_PER_ENRICH=2
supabase secrets set ODDS_API_ENRICH_REGIONS=us
supabase secrets set ENABLE_MARKET_ENRICHMENT=true
```

## 3. Schedule The Function

Run `supabase_edge_cron_setup.sql` after replacing:

- `<project-ref>`
- `<replace-with-ODDS_INGEST_FUNCTION_SECRET>`

The schedule is every 10 minutes (144 runs/day). The Edge Function enforces a strict per-run credit ceiling (`ODDS_MAX_CREDITS_PER_RUN`) and rotates the main pulls and the expensive derivative/alternate/player-prop enrichment across cycles so the monthly budget (~20k credits) is respected. Use Supabase cron controls to pause it on non-game days or narrow `ODDS_API_ACTIVE_SPORTS`.

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

