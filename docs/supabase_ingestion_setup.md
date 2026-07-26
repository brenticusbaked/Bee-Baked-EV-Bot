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
# In-season leagues are auto-detected from the universe (see "Sport selection"
# below) — you normally do NOT set a sports list. Optionally override the universe:
supabase secrets set ODDS_API_SPORT_UNIVERSE=baseball_mlb,basketball_wnba,basketball_nba,icehockey_nhl,americanfootball_nfl,tennis
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
# no prop could alert. The ceiling is a SAFETY CAP, not the spend driver — the
# game-proximity throttle only pulls a sport near its next game, so realized spend
# runs well under runs/day x ceiling. Default depth is 48/run and 4 enriched
# events/run to cover more props/alternates while the throttle keeps monthly spend
# near the 20k tier. Watch odds_ingest_runs.credits_used and dial back if it
# trends much above ~600/day.
supabase secrets set ODDS_MAX_CREDITS_PER_RUN=48
# Per-event enrichment (derivatives / alternates / player props) is fetched from
# the per-event odds endpoint and rotates across runs by a time-based slot.
supabase secrets set ODDS_MAX_EVENTS_PER_ENRICH=4

# Auto-expiring end-of-month burn: while today's UTC date is on or before
# ODDS_BURN_UNTIL, ingestion uses the deeper burn caps and bypasses the proximity
# throttle to spend down remaining credits; it reverts to the steady-state
# defaults above by itself once the date passes (no manual revert / redeploy).
# Leave unset to disable. Tune intensity with the two burn caps.
supabase secrets set ODDS_BURN_UNTIL=2026-07-31
supabase secrets set ODDS_BURN_MAX_CREDITS_PER_RUN=150
supabase secrets set ODDS_BURN_MAX_EVENTS_PER_ENRICH=6
supabase secrets set ODDS_API_ENRICH_REGIONS=us
supabase secrets set ENABLE_MARKET_ENRICHMENT=true

# --- Game-proximity throttle (credit concentration) ---
# On each cron tick the function looks up the nearest upcoming/live game per
# sport (from cached `fixtures`, 0 API credits) and only pulls a sport when its
# game is close enough to be "due" this tick. This reproduces a "poll faster as
# the game approaches" schedule on the serverless pg_cron model — no long-running
# process — and can only ever REDUCE spend vs. pulling every sport every tick.
# Tiers (hours-until-game -> minutes-between-pulls), all overridable:
#   > 24h            -> 240 min (sparse; also refreshes fixtures/openers)
#   12-24h           ->  60 min
#   2-12h            ->  15 min
#   <= 2h (or live)  -> every tick
# Nearest game unknown (no fixtures yet) -> 60 min discovery cadence.
supabase secrets set ODDS_PROXIMITY_THROTTLE=true       # set false to pull all sports every tick
supabase secrets set ODDS_PROXIMITY_FAR_HOURS=24
supabase secrets set ODDS_PROXIMITY_MID_HOURS=12
supabase secrets set ODDS_PROXIMITY_NEAR_HOURS=2
supabase secrets set ODDS_POLL_FAR_MINUTES=240
supabase secrets set ODDS_POLL_MID_MINUTES=60
supabase secrets set ODDS_POLL_CLOSE_MINUTES=15
supabase secrets set ODDS_POLL_UNKNOWN_MINUTES=60
```

When no sport is due on a tick, the function writes a zero-credit
`odds_ingest_runs` row with `status = 'skipped'` (so the schedule is visibly
ticking rather than silently dead) and makes no Odds API calls. To get finer
near-game resolution (closer to per-minute as tip-off approaches), tighten the
prime-hours cron in `supabase_edge_cron_setup.sql`; the throttle keeps the
extra ticks cheap because distant-game sports stay gated off.

**Forcing a full pull.** A manual test can land on a "skipped" slot. To bypass
the throttle and pull every active sport immediately (e.g. to verify the
pipeline or backfill), send `{"force": true}` — or any `trigger` starting with
`manual` — in the request body:

```sql
select net.http_post(
    url := 'https://<project-ref>.supabase.co/functions/v1/odds-cache-ingest',
    headers := jsonb_build_object('Content-Type','application/json','x-ingest-secret','<INGEST_SECRET>'),
    body := jsonb_build_object('trigger','manual_test')  -- or jsonb_build_object('force', true)
);
```

A forced/manual run also **enriches every active event's props/alternates**
(not the rotating per-run subset) so the scan and execution desk price off
freshly-pulled odds instead of a stale cached line. The pipeline's pre-scan
trigger (`{"trigger":"manual_pipeline","force":true}`) uses this. Forced runs use
a higher credit ceiling — `ODDS_FORCE_MAX_CREDITS_PER_RUN` (default 500) — so the
full enrich isn't cut short; the primary key is still hard-capped at the monthly
tier, so this can't overspend it.

Scheduled cron jobs use `trigger = 'pg_cron'` / `'pg_cron_opener'`, so they
still respect the throttle.

Player-prop coverage (`SPORT_EXTRAS` in the Edge Function) spans the standard
counting-stat Over/Under props for each sport (NFL passing/rushing/receiving
and defense/kicking props, MLB pitcher/batter props, NBA/WNBA
points/rebounds/assists/threes/combos, NHL points/goals/assists/SOG/saves) plus
team alternate spreads/totals and quarter/half derivatives. Markets are split into
small groups that rotate across cron slots so no single group blows the per-run
ceiling. You can override the market bundles with:

```bash
supabase secrets set ODDS_API_NFL_PROP_MARKETS=...
supabase secrets set ODDS_API_WNBA_PROP_MARKETS=...
```

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
WNBA +4.1% (n=472), NBA +3.5% (n=2574), NHL +14% (small n).

**In-season detection is automatic — you do NOT maintain a sports list.** The
function keeps a fixed *universe* of leagues and, every run, keeps only the ones
that actually have a **real upcoming game**. It checks the free
`/v4/sports/{league}/events` endpoint (0 credits) — which lists only dated games,
never futures/outrights — and includes a league when it has a game within the
next ~8 days (`ODDS_SEASON_HORIZON_HOURS`, default 192). This is deliberately
NOT the `/v4/sports` "active" flag: that flag stays true year-round for leagues
posting Super Bowl / Stanley Cup futures, so gating on it would burn main-pull
credits on NFL/NHL in the dead of summer. So MLB/WNBA run in summer, NBA/NHL/NFL
switch on by themselves the moment their seasons have games, off-season leagues
cost nothing, and you never edit a secret by season. Default universe (ROI
priority order):

```
baseball_mlb,basketball_wnba,basketball_nba,icehockey_nhl,americanfootball_nfl,tennis
```

Override the universe only if you want a different set of leagues:

```bash
supabase secrets set ODDS_API_SPORT_UNIVERSE=baseball_mlb,basketball_wnba,basketball_nba,icehockey_nhl,americanfootball_nfl,tennis
```

The legacy `ODDS_API_ACTIVE_SPORTS` secret is **ignored** while auto-detect is on
(the default). To go back to a hand-pinned list, set
`ODDS_API_AUTODETECT_SPORTS=false` and it will use `ODDS_API_ACTIVE_SPORTS`
(still season-filtered and tennis-expanded). You can safely delete
`ODDS_API_ACTIVE_SPORTS` in the default mode.

The `tennis` token (also `tennis_atp` / `tennis_wta`) is expanded at runtime into
the currently-active tournament keys via the free `/v4/sports` listing, since
tennis keys are per-tournament (`tennis_atp_wimbledon`, ...) and rotate weekly.
Tennis is pulled on the moneyline (`h2h`) only — ~1 market × regions per active
tournament (~2 credits/run each, override with `ODDS_API_TENNIS_MARKETS`), so
watch the budget when several tournaments overlap the MLB/WNBA slate. Tennis
alerts are gated by `ENABLE_TENNIS_ALERTS` (default on) and route to
`DISCORD_TENNIS_BETS_WEBHOOK_URL` (falls back to the default bet-alerts channel).

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

## 3b. Multi-sport expansion + late-night scoping

New env toggles on the `odds-cache-ingest` Edge Function (no redeploy needed):

- `ODDS_UPCOMING_HORIZON_HOURS` (default `48`): how far ahead enrichment
  (props/alternates) reaches. Raised from 30h so the 03:30 UTC late-night run
  fetches tomorrow's opening lines.
- `ENABLE_TENNIS_SCAN` (default `true`), `ENABLE_SOCCER_SCAN` (default `false`),
  `SOCCER_LEAGUES_FILTER` (default `soccer_epl,soccer_usa_mls,soccer_uefa_champs_league,soccer_spain_la_liga`).
  Every soccer/tennis candidate is still `/events`-gated (0 credits), so an
  off-season league with no games costs nothing.
- `ODDS_SOCCER_PROP_MARKETS` (default `player_shots_on_target,player_shots`).

Python scan side: `ENABLE_SOCCER_ALERTS`, `ENABLE_TENNIS_ALERTS`,
`ENABLE_L10_CONTEXT` (append "Last 10" hit-rate to prop slips, cache-only).

## 3c. Contextual Stat Enrichment Engine

`supabase_historical_stats_schema.sql` adds `mlb_/nba_/wnba_/nfl_/soccer_player_logs`
and `tennis_match_logs`. The `Daily Stat Ingest` workflow
(`.github/workflows/daily_stat_ingest.yml`, 09:00 UTC / 4 AM CDT) runs
`daily_stat_ingest.py` → `services/stat_ingest.py`, which fetches yesterday's box
scores from free libraries (pybaseball, nba_api, nfl_data_py, soccerdata,
Sackmann tennis CSVs) and upserts via `db_manager.upsert_player_logs`. The live
scan reads these through `db_manager.get_l10_hit_rate` only — it never calls an
external stat API synchronously.

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

