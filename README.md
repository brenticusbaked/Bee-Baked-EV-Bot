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
* `DISCORD_DAILY_SLIPS_WEBHOOK_URL` (Optional: sends the daily CLV + win/loss slip report to a separate Discord channel)
* `SUPABASE_URL`
* `SUPABASE_KEY` (Use the `service_role` key to bypass Row-Level Security)

**Optional Threshold Overrides:**
* `UNIFIED_EV_THRESHOLD`
* `UNIFIED_NEAR_MISS_THRESHOLD`
* `UNIFIED_SPREAD_EV_THRESHOLD`
* `UNIFIED_H2H_EV_THRESHOLD`
* `UNIFIED_TOTAL_EV_THRESHOLD`
* `LINE_MOVEMENT_MAX_BOOST`
* `LINE_MOVEMENT_MAX_PENALTY`
* `PROP_EV_THRESHOLD`
* `PROP_NEAR_MISS_THRESHOLD`
* `NBA_PROP_STATS` (Optional comma-separated override. Example: `points,assists,rebounds,three_pointers,steals,blocks,turnovers,points_rebounds_assists`)
* `SGO_GRADER_MAX_FETCHES`
* `SGO_GRADER_FETCH_DELAY_SECONDS`
* `CLV_LOOKBACK_DAYS`
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
* `ENABLE_SGO_GRADER` (Default: `true`, but disabled in the core workflow so the NBA prop bot gets priority on a single SGO key)
* `ENABLE_PERFORMANCE_REPORT` (Default: `false`)
* `ENABLE_PYBASEBALL_FIP_SCRAPER` (Default: `true`, but disabled in the scheduled scraper workflow until capture is stable)
* `ENABLE_DRAFTKINGS_SCRAPER` (Default: `true`, but currently disabled in the scheduled scraper workflow while it remains experimental)
* `ENABLE_BETMGM_SCRAPER` (Default: `true`, and re-enabled in the scheduled scraper workflow with proxy rotation plus broader payload capture)
* `ENABLE_FANDUEL_SCRAPER` (Default: `true`, and re-enabled in the scheduled scraper workflow with a direct content-managed fallback plus broader payload capture)
* `ENABLE_PRIZEPICKS_SCRAPER` (Default: `true`, but disabled in the scheduled scraper workflow until a stable access path is restored)

**Automation:** The `.github/workflows/main.yml` file contains the lean core EV cron schedule (`0 8,16 * * *`) that executes `master_run.py` twice daily (8:00 AM & 4:00 PM UTC). Scrapers run separately in `.github/workflows/scrapers.yml` at `30 8,16 * * *`. Right now, FanDuel and BetMGM are the two re-enabled rebuilt sources in the scheduled scraper run. DraftKings remains experimental, and PrizePicks plus FanGraphs/FIP remain disabled by default until their access paths are stable again. A master odds cache is pulled at the beginning of each core run using NBA spreads, NHL spreads, and MLB H2H only, which keeps the primary footprint to 6 API credits per execution (12 per day). If `ODDS_API_KEY_2` is provided, a secondary expansion pull is merged into the same cache using NBA H2H + totals, NHL H2H, and MLB first-5 H2H for 8 additional credits per execution (16 per day on the second key). If `ODDS_API_KEY_3` is provided, a tertiary expansion pull is merged into the same cache using NHL totals plus MLB spreads and totals for 6 additional credits per execution (12 per day on the third key). MLB H2H remains available for the MLB model, but unified MLB H2H alerts are off by default unless `ENABLE_MLB_H2H_ALERTS=true`. NBA totals, NHL totals, MLB spreads, and MLB totals can all be toggled independently with feature flags. The SGO grader now runs best as its own workflow in `.github/workflows/sgo_grader.yml`, with a very small fetch budget by default, which helps preserve a single SGO key for the NBA prop bot during the core live run. The daily slips summary runs once per day from `.github/workflows/daily_slips.yml` at `13:15 UTC` and now reports slips placed, bets settled, and CLV updates based on their actual timestamps instead of only the original bet date.
