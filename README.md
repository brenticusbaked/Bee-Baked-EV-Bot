# Bee-Baked-EV-Bot: The Ultimate 2026 Sports Syndicate

This repository contains a fully automated, high-frequency sports betting ecosystem designed to identify, log, and track +EV (Expected Value) opportunities across all major sports markets. 

By combining real-time API scanning, headless browser scraping, and advanced situational models into a **serverless cloud database**, the Bee-Baked system operates as a professional-grade betting syndicate on autopilot.

## 🚀 The Core Architecture
The bot is organized into a modular pipeline, communicating seamlessly with a PostgreSQL cloud backend (Supabase) to completely eliminate file-locking bottlenecks:

### Data Acquisition (The Scanners)
* **Unified Scanner:** A centralized, API-limit-protected bot that scans H2H, Spreads, and Totals for NBA, NHL, MLB, Soccer, and more.
* **Smart Props Engine:** Utilizes the SportsGameOdds API to find player props (Points, Assists, etc.) by comparing sharp Pinnacle lines against soft retail books for edges > 2%.
* **Headless Scraping:** Uses Playwright to scrape "soft" books like Bovada and Kambi-powered sites (BetRivers, Unibet) by mimicking human browser behavior and dodging bot protection.

### Edge Logic (The Predictive Models)
* **NBA Travel Fatigue Model:** Goes beyond simple back-to-backs. This model calculates a "Fatigue Severity" differential to catch fully-rested home teams hosting opponents on brutal road-to-road travel schedules.
* **MLB Advanced Metrics (FIP):** Ignores public ERA (which is priced into sportsbooks) and calculates **Estimated FIP** using raw K/BB/HR rates to find massively undervalued starting pitchers in the First 5 Innings (F5) market.
* **NHL Goal Differential:** Targets massive team quality gaps (> 40 goal differential) to hunt for high-value -1.5 Puck Lines.

### Automated Management & Auditing
* **Cloud DB Manager:** All models route through `db_manager.py` to seamlessly push and pull data from a Supabase cloud ledger, allowing infinite parallel scaling.
* **CLV Tracker:** Monitors Pinnacle (the sharpest book) to log the Closing Line Value for every bet.
* **Auto-Grader:** Automatically fetches SGO box scores to grade past bets as a Win, Loss, or Push.
* **Sharp Metrics (CLV Analyzer):** Analyzes your entire betting history to calculate your "CLV Beaten %" and "Average Edge vs Close"—mathematical proof that the syndicate is beating the house.

### Execution Management & Smart Order Routing
The repo now includes a paper-trading execution desk that turns +EV signals into parent orders, applies risk limits, splits child orders across multiple venues, simulates fills, and produces transaction-cost analytics.

* **EMS Parent Orders:** `execution.models.ParentOrder` captures symbol, side, quantity, limit/fair price, source signal, and strategy metadata.
* **Smart Order Router:** `execution.router.SmartOrderRouter` ranks venues by executable price, available quantity, latency, fill probability, and fees. It supports normal lower-price-is-better routing and sports-odds higher-payout-is-better routing.
* **Multi-Venue Paper Adapters:** `execution.venues.PaperVenueAdapter` provides deterministic venue simulation until real authenticated adapters are intentionally added.
* **Risk Controls:** `execution.risk.RiskManager` blocks orders that exceed quantity, notional, symbol exposure, or minimum-edge limits.
* **TCA / Execution Quality:** `execution.tca.execution_metrics` reports fill rate, average fill price, slippage, edge capture, and fees.
* **Pipeline Bridge:** `execution_scanner.py` consumes the existing master odds cache and paper-routes qualifying +EV opportunities into `execution_ledger.json`.
* **Supabase Persistence:** Execution reports are written to `execution_orders`, `execution_child_orders`, `execution_fills`, and `venue_metrics` when Supabase is configured. If Supabase is unavailable, reports queue in `pending_execution_reports.json`.
* **Venue Analytics:** `execution_analytics.py` summarizes venue fill rate, average fill price, routed quantity, fees, and average edge capture from Supabase or a local ledger.

Run the standalone demo:

```bash
python execution_desk.py
```

Summarize venue performance from Supabase:

```bash
python execution_analytics.py
```

Validate that the latest GitHub Actions paper run wrote Supabase execution rows:

```bash
python execution_healthcheck.py
```

For a no-odds-API proof of Supabase execution persistence, run the **Execution Desk Calibration** workflow in GitHub Actions. It writes one synthetic paper order tagged `synthetic_calibration` and then runs the healthcheck.

Or from a local paper ledger:

```bash
python execution_analytics.py --ledger execution_ledger.json
```

Run the execution bridge in the scheduled pipeline by setting `ENABLE_EXECUTION_DESK=true`. It is disabled by default and paper-only by design.

Before enabling Supabase execution persistence, run `supabase_execution_schema.sql` in the Supabase SQL editor.

## ⚡ Discord Notifications
The bot acts as a live dispatcher, pinging your Discord with structured alerts:
* 🟢 **Routine Alerts:** Standard +EV opportunities and situational model mismatches.
* 🔴 **Emergency Alerts:** Detected prop edges greater than 6% that require immediate hammering.
* 🚑 **Breaking News:** Live injury updates and lineup scratches intercepted from RSS feeds.

## 🛠️ Setup & Deployment
This syndicate is designed to run completely serverless using GitHub Actions, while staying 100% within the free tiers of The Odds API and SportsGameOdds.

**Requirements:** Install the environment using `pip install -r requirements.txt`. Playwright is required for headless scraping.

**Environment Variables:** Add the following to your GitHub Repository Secrets:
* `ODDS_API_KEY`
* `ODDS_API_KEY_2` (Optional: targeted secondary Odds API expansion for extra markets)
* `ODDS_API_KEY_3` (Optional: tertiary Odds API expansion for NHL totals and MLB spreads/totals)
* `SGO_API_KEY`
* `DISCORD_WEBHOOK_URL`
* `DISCORD_STATUS_WEBHOOK_URL` (Optional: sends workflow-complete status updates to a separate Discord channel)
* `DISCORD_INJURY_WEBHOOK_URL` (Optional: sends injury/news alerts to a separate Discord channel)
* `DISCORD_MLB_UPDATES_WEBHOOK_URL` (Optional: routes MLB starters, lineups, and injury updates)
* `DISCORD_NHL_UPDATES_WEBHOOK_URL` (Optional: routes NHL injury/news updates)
* `DISCORD_NFL_UPDATES_WEBHOOK_URL` (Optional: routes NFL injury/news updates)
* `DISCORD_WNBA_UPDATES_WEBHOOK_URL` (Optional: routes WNBA injury/news updates)
* `DISCORD_SOCCER_UPDATES_WEBHOOK_URL` (Optional: routes soccer injury/news updates)
* `DISCORD_WNBA_BETS_WEBHOOK_URL` (Optional: routes WNBA +EV bet alerts; falls back to WNBA updates, then default alerts)
* `DISCORD_DAILY_SLIPS_WEBHOOK_URL` (Optional: sends the daily CLV + win/loss slip report to a separate Discord channel)
* `SUPABASE_URL`
* `SUPABASE_KEY` (Use the `service_role` key to bypass Row-Level Security)

**Optional Threshold Overrides:**
* `UNIFIED_EV_THRESHOLD`
* `UNIFIED_NEAR_MISS_THRESHOLD`
* `UNIFIED_SPREAD_EV_THRESHOLD`
* `UNIFIED_H2H_EV_THRESHOLD`
* `UNIFIED_TOTAL_EV_THRESHOLD`
* `UNIFIED_MAX_ALERTS_PER_EVENT_MARKET`
* `NEWS_FEED_SPORTS` (Optional comma-separated sports list. Default: `NBA,MLB,NHL,NFL,WNBA,SOCCER`)
* `LINE_MOVEMENT_MAX_BOOST`
* `LINE_MOVEMENT_MAX_PENALTY`
* `PROP_EV_THRESHOLD`
* `PROP_NEAR_MISS_THRESHOLD`
* `NBA_PROP_STATS` (Optional comma-separated override. Example: `points,assists,rebounds,three_pointers,steals,blocks,turnovers,points_rebounds_assists`)
* `SCRAPER_PROXY_ATTEMPTS`
* `BETMGM_SCRAPER_PROXY_ATTEMPTS`
* `BETMGM_DIRECT_TIMEOUT_MS`
* `BETMGM_LAUNCH_TIMEOUT_MS`
* `BETMGM_NAV_TIMEOUT_MS`
* `BETMGM_WAIT_CYCLES`
* `BETMGM_WAIT_MS`
* `BETMGM_ENABLE_BROWSER_FALLBACK`
* `BETMGM_PREFERRED_STATE`
* `SGO_GRADER_MAX_FETCHES`
* `SGO_GRADER_FETCH_DELAY_SECONDS`
* `CLV_LOOKBACK_DAYS`
* `EXECUTION_EV_THRESHOLD`
* `EXECUTION_CALIBRATION_ORDERS` (Optional paper-only proof-of-write routes when live opportunities do not clear threshold. Default: `0`)
* `EXECUTION_MAX_ORDERS`
* `EXECUTION_MAX_ORDER_UNITS`
* `EXECUTION_MAX_NOTIONAL`
* `EXECUTION_LEDGER_PATH`
* `PENDING_EXECUTION_REPORTS_PATH`
* `MLB_FIP_GAP_THRESHOLD`
* `MLB_MODEL_EDGE_THRESHOLD`
* `NBA_MODEL_EDGE_THRESHOLD`
* `NHL_GD_GAP_THRESHOLD`
* `NHL_MODEL_EDGE_THRESHOLD`

**Optional Feature Flags:**
* `ENABLE_NEWS` (Default: `true`)
* `ENABLE_NBA_PROP_BOT` (Default: `true`)
* `ENABLE_NBA_MODEL` (Default: `true`)
* `ENABLE_NHL_MODEL` (Default: `true`)
* `ENABLE_MLB_MODEL` (Default: `true`)
* `ENABLE_UNIFIED_SCAN` (Default: `true`)
* `ENABLE_MLB_H2H_ALERTS` (Default: `false`)
* `ENABLE_NBA_TOTAL_ALERTS` (Default: `true`)
* `ENABLE_NHL_TOTAL_ALERTS` (Default: `true`)
* `ENABLE_MLB_SPREAD_ALERTS` (Default: `true`)
* `ENABLE_MLB_TOTAL_ALERTS` (Default: `true`)
* `ENABLE_CLV_TRACKER` (Default: `true`)
* `ENABLE_EXECUTION_DESK` (Default: `false`, paper-routes qualifying opportunities through the EMS/SOR layer)
* `ENABLE_SGO_GRADER` (Default: `true`, but disabled in the core workflow so the NBA prop bot gets priority on a single SGO key)
* `ENABLE_PERFORMANCE_REPORT` (Default: `false`)
* `ENABLE_PYBASEBALL_FIP_SCRAPER` (Default: `true`, but disabled in the scheduled scraper workflow until capture is stable)
* `ENABLE_DRAFTKINGS_SCRAPER` (Default: `true`, but currently disabled in the scheduled scraper workflow while it remains experimental)
* `ENABLE_BETMGM_SCRAPER` (Default: `true`, but currently disabled in the scheduled scraper workflow while runtime and capture stability are tuned)
* `ENABLE_FANDUEL_SCRAPER` (Default: `true`, and re-enabled in the scheduled scraper workflow with a direct content-managed fallback plus broader payload capture)
* `ENABLE_PRIZEPICKS_SCRAPER` (Default: `true`, but disabled in the scheduled scraper workflow until a stable access path is restored)

**Automation:** The `.github/workflows/main.yml` file contains the lean core EV cron schedule (`0 10,14,20 * * *`) that executes `master_run.py` three times daily. During Central daylight time, that lands at roughly 5:00 AM, 9:00 AM, and 3:00 PM CT to catch overnight market shape, morning liquidity, and afternoon lineup/steam movement. Scrapers run separately in `.github/workflows/scrapers.yml` at `30 10,14,20 * * *`, 30 minutes after each core run. Right now, FanDuel is the only scraper enabled in the scheduled scraper run by default. BetMGM has been rebuilt but is currently tested through the manual `.github/workflows/betmgm_test.yml` workflow while runtime and capture stability are tuned; that workflow now tries the sportsbook API first, then direct/proxied HTML, and finally one stealth-enabled proxy-backed browser path that starts from the BetMGM home page, attempts the preferred state selection, and then opens the NBA page. DraftKings remains experimental, and PrizePicks plus FanGraphs/FIP remain disabled by default until their access paths are stable again. A master odds cache is pulled at the beginning of each core run using NBA spreads, WNBA spreads, NHL spreads, and MLB H2H, which keeps the primary footprint to 8 API credits per execution (24 per day). If `ODDS_API_KEY_2` is provided, a secondary expansion pull is merged into the same cache using NBA H2H + totals, WNBA H2H + totals, NHL H2H, and MLB first-5 H2H for 12 additional credits per execution (36 per day on the second key). If `ODDS_API_KEY_3` is provided, a tertiary expansion pull is merged into the same cache using NHL totals plus MLB spreads and totals for 6 additional credits per execution (18 per day on the third key). MLB H2H remains available for the MLB model, but unified MLB H2H alerts are off by default unless `ENABLE_MLB_H2H_ALERTS=true`. NBA totals, WNBA markets, NHL totals, MLB spreads, and MLB totals can all be scanned by the unified +EV engine. The SGO grader now runs best as its own workflow in `.github/workflows/sgo_grader.yml`, with a very small fetch budget by default, which helps preserve a single SGO key for the NBA prop bot during the core live run. The daily slips summary runs once per day from `.github/workflows/daily_slips.yml` at `13:15 UTC` and now reports slips placed, bets settled, and CLV updates based on their actual timestamps instead of only the original bet date.
