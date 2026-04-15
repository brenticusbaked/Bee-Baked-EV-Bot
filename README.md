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
* `SGO_API_KEY`
* `DISCORD_WEBHOOK_URL`
* `SUPABASE_URL`
* `SUPABASE_KEY` (Use the `service_role` key to bypass Row-Level Security)

**Automation:** The `.github/workflows/main.yml` file contains a pre-configured cron schedule (`0 8,16 * * *`) that executes the `master_run.py` script twice daily (8:00 AM & 4:00 PM UTC). A Master Odds Cache is pulled at the beginning of each run to ensure all predictive models utilize identical market data while limiting the footprint to just 6 API credits per execution.