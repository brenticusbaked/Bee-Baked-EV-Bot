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
# The Odds API bills per region (or per 10 books), NOT per individual book, so up
# to 10 books is free. More books = broader consensus baseline for props + more
# soft-book mispricings caught. Keep pinnacle first (eu sharp baseline) and the
# list at <=10 (books past 10 bill as an extra region-equivalent).
supabase secrets set ODDS_API_TARGET_BOOKS=pinnacle,fanduel,draftkings,betmgm,bet365,caesars,bovada,espnbet,fanatics,betrivers
```

Extended US exchange coverage (Novig etc.) — these live in the `us_ex` region,
billed separately. They add the betting **exchanges**, which are used almost
exclusively by the arbitrage scanner; since scheduled arb alerts are off, the
extended pull is **disabled by default** (`ODDS_EXTENDED_EVERY_N_SLOTS=0`) so
those credits go to the sharp/soft prop pulls instead. Cost is per region, not
per book, so Kalshi/Polymarket/ProphetX ride along for free with Novig if you
re-enable it for manual arb:

```bash
supabase secrets set ODDS_API_EXTENDED_REGIONS=us_ex
supabase secrets set ODDS_API_EXTENDED_BOOKS=novig,kalshi,polymarket,prophetx
# Run the extended pull every Nth 10-min slot (3 = every 30 min). 0 disables it
# (default: disabled — re-enable only for manual arbitrage).
supabase secrets set ODDS_EXTENDED_EVERY_N_SLOTS=0
# To also add Fliff / ESPN BET (ex-theScore), which live in the separate `us2`
# region (extra per-region credit): ODDS_API_EXTENDED_REGIONS=us_ex,us2 and add
# fliff,espnbet to ODDS_API_EXTENDED_BOOKS. theScore Bet is now ESPN BET (espnbet).
# Onyx Odds, and standalone Kalshi/Polymarket beyond us_ex, are not otherwise
# offered by The Odds API.
```

Optional budget/expansion controls (defaults shown):

```bash
# Per-run credit ceiling. Each player-prop group needs BOTH a sharp (Pinnacle/EU)
# and a soft (US) per-event pull, budgeted as an atomic pair, so the ceiling must
# clear ~22 (2 sports main = 12 + one prop pair = 10) for props to land at all.
# The old 14 could only fund the sharp half, so soft-book props never landed and
# no prop could alert. Paired with the game-hours cron (~31 runs/day) 24 lands
# ~20k credits/month. Raise/lower to match cron cadence and ODDS_API_ACTIVE_SPORTS.
supabase secrets set ODDS_MAX_CREDITS_PER_RUN=24
# Per-event enrichment (derivatives / alternates / player props) is fetched from
# the per-event odds endpoint and rotates across runs by a time-based slot.
supabase secrets set ODDS_MAX_EVENTS_PER_ENRICH=2
supabase secrets set ODDS_API_ENRICH_REGIONS=us
supabase secrets set ENABLE_MARKET_ENRICHMENT=true
```

Player-prop coverage (`SPORT_EXTRAS` in the Edge Function) spans the standard
counting-stat Over/Under props for each sport (MLB pitcher/batter props, NBA/WNBA
points/rebounds/assists/threes/combos, NHL points/goals/assists/SOG/saves) plus
team alternate spreads/totals and quarter/half derivatives. Markets are split into
small groups (≤5 markets each) that rotate across cron slots so no single group
blows the per-run ceiling.

Because every prop is priced against the **Pinnacle** baseline (multiplicative
de-vig; `.windsurfrules` Rule 1), each enrich group is pulled twice — once from
`eu`/`pinnacle` (sharp baseline, `ODDS_API_ENRICH_SHARP_REGIONS`) and once from
`us`/rec books (`ODDS_API_ENRICH_REGIONS`) — kept as separate requests per Rule 2,
but **budgeted together as an atomic pair**: the run reserves the full sharp+soft
cost before starting, so it never pays for a sharp pull it can't complete with the
matching soft pull (the bug that left soft-book props missing under the old
14-credit ceiling). The cache merges them back per fixture. Without the soft pull
there is nothing to compare to the Pinnacle baseline, so no prop can alert.
Coverage is limited to markets Pinnacle actually posts
as clean two-way props; alternate player-prop ladders, first-basket, anytime-
scorer and double/triple-double are intentionally excluded (the Pinnacle-baseline
engine cannot price them — that needs a distribution model off the main line).

Adding market breadth does **not** raise the monthly credit total on its own — the
`ODDS_MAX_CREDITS_PER_RUN` ceiling is fixed, so more markets simply share the same
budget via rotation (breadth vs. per-line refresh frequency). Widening
`ODDS_API_TARGET_BOOKS` up to 10 books is free (billed per region, not per book).
To refresh props faster, raise the ceiling and/or `ODDS_MAX_EVENTS_PER_ENRICH` and
watch `odds_ingest_runs.credits_used`. Unsupported / out-of-season markets return
404/422 and are treated as billed no-ops.

Budget is concentrated on the sports the syndicate is historically best at and on
the hours markets are actually up. From the transaction history, realized ROI on
straight bets the engine would flag (predicted EV > 0) is: MLB +7.1% (n=1720),
WNBA +4.1% (n=472), NBA +3.5% (n=2574), NHL +14% (small n). Keep the strongest
**in-season** sports first (the first sport is favored by the always-run-first
rule) and trim idle leagues so their main pulls don't burn credits:

```bash
# In-season now (summer). Add basketball_nba,icehockey_nhl when their seasons
# resume — and either raise the ceiling or slow the cron, since each extra sport
# adds ~6 credits/run to the main-market baseline.
supabase secrets set ODDS_API_ACTIVE_SPORTS=baseball_mlb,basketball_wnba
```

The cron in `supabase_edge_cron_setup.sql` runs every 30 min during prime game
hours (16:00-04:59 UTC ≈ 11am-midnight CT), every 60 min in the morning pregame
window (11:00-15:59 UTC), and **pauses overnight** (05:00-09:29 UTC ≈ 12am-4:30am CT)
since no bets are placed then. One exception: a single **opener** ingest at
09:30 UTC (≈4:30am CT) captures the freshest opening lines, which the
`Overnight Opener Scan` workflow (09:45 UTC) reads and posts to the opener stream
tagged `[OPENER]` for morning review. The continuous +EV alert workflow is paused
overnight; only the once-daily opener scan fires in that window.

### Alert dedup & opposite-side suppression

The scanner (`unified_bot.scan_markets` / `evaluate_player_props`) will not send
the same bet twice or both sides of the same wager:

- **Duplicate bets** — a selection already logged today for the same
  event+market is skipped (`is_already_logged`).
- **Opposite sides** — only one directional side is alerted per event+market:
  never both moneylines, never both a spread and its mirror, never Over **and**
  Under of the same total or the same player-prop line. A correctly de-vigged
  Pinnacle baseline has at most one +EV side, so both sides showing as +EV means
  stale/mismatched data — the higher-edge side wins and the opposite is dropped.
  This holds both **within a run** (best side chosen) and **across runs** (the
  opposite of a side already alerted today is suppressed via the open-bet log).

## 3. Schedule The Function

Run `supabase_edge_cron_setup.sql` after replacing:

- `<project-ref>`
- `<replace-with-ODDS_INGEST_FUNCTION_SECRET>`

The schedule runs during game hours only, plus one overnight opener pull (~32 runs/day): every 30 min in prime hours, every 60 min mornings, paused overnight. The Edge Function enforces a strict per-run credit ceiling (`ODDS_MAX_CREDITS_PER_RUN`) and rotates the main pulls and the expensive derivative/alternate/player-prop enrichment (sharp+soft pairs) across cycles so the monthly budget (~20k credits) is respected. Use Supabase cron controls to pause it on non-game days or narrow `ODDS_API_ACTIVE_SPORTS`.

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

