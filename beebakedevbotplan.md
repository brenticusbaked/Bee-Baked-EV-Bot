# BEE BAKED BETS — The Hive +EV Scanner

## Master Architecture & Roadmap

---

## Project Goal

Build and maintain a Python-based **+EV (Expected Value) sports betting scanner** for the "BEE BAKED BETS" Discord community (a.k.a. **The Hive**).

The scanner:

1. Pulls **sharp odds from Pinnacle** to establish a mathematical source of truth (true probability).
2. Compares those against **US recreational sportsbooks** to find +EV discrepancies.
3. Calculates a **Quarter-Kelly unit size** for bankroll management.
4. Sends formatted **Discord Webhook alerts** to The Hive with full math transparency.

---

## Data Architecture

### Source of Truth — Pinnacle (Sharp Baseline)

- **API:** [The Odds API](https://api.the-odds-api.com/v4/sports) — EU region.
- **Bookmaker filter:** `pinnacle` only.
- Pinnacle is the sharpest publicly available sportsbook. Its lines reflect the most efficient market pricing and serve as the baseline for deriving true probabilities.

### Recreational Lines — US Sportsbooks

- **API:** The Odds API — US region.
- **Target books:** `draftkings`, `fanduel`, `betmgm`, `caesars`, `bet365`.
- These books carry wider margins (more vig), creating exploitable +EV opportunities when their lines diverge from the true probability.

### Data Flow

```
The Odds API (EU region)          The Odds API (US region)
        │                                  │
        ▼                                  ▼
  Pinnacle lines ──────┐      Recreational lines ──────┐
  (sharp baseline)     │      (DK, FD, BetMGM, etc.)   │
                       ▼                                ▼
              ┌─────────────────────────────────────────────┐
              │          De-Vig & EV Comparison Engine       │
              │  1. Convert to implied probabilities          │
              │  2. Remove vig → true probability             │
              │  3. EV% = true_prob × offered_decimal − 1    │
              │  4. Quarter-Kelly sizing                      │
              └──────────────────┬──────────────────────────┘
                                 │
                                 ▼
                     Discord Webhook Alert
                     (+EV Alert from The Hive)
```

### Exclusions

> **Overtime Markets / AMM data must NOT be used as the sharp baseline.**
>
> Overtime Markets operate via an Automated Market Maker (AMM). AMM liquidity slippage causes prices to deviate from true market efficiency, which will **break the EV math** by skewing the derived true probability. Only traditional exchange/bookmaker lines (specifically Pinnacle via The Odds API) are valid baselines.

---

## Mathematical Pipeline

The scanner follows an exact 4-step mathematical pipeline. Each step must be implemented precisely to avoid skewing bankroll sizing.

### Step 1 — Convert American Odds to Implied Probabilities

American odds are converted to decimal odds, then to raw implied probabilities:

```
Decimal odds:
  If American > 0:   decimal = (American / 100) + 1
  If American < 0:   decimal = (100 / |American|) + 1

Implied probability:
  implied_prob = 1 / decimal_odds
```

**Example:** Pinnacle posts Team A at -150, Team B at +130.

| Side   | American | Decimal | Implied Prob |
|--------|----------|---------|-------------|
| Team A | -150     | 1.667   | 59.94%      |
| Team B | +130     | 2.300   | 43.48%      |
| **Sum**|          |         | **103.42%** |

The sum exceeds 100% — the excess is the **vig (juice)**.

### Step 2 — Calculate Market Margin (Vig)

```
Vig = sum(implied_probabilities) − 1.0
```

In the example above: `Vig = 1.0342 − 1.0 = 3.42%`

### Step 3 — De-Vig to Find True Probability

Remove the vig proportionally from each side to derive the **true probability**.

#### Multiplicative (Proportional) Method

```
true_prob_i = implied_prob_i / sum(all_implied_probs)
```

This normalizes probabilities to sum to exactly 1.0.

#### Power Method (Default)

The power method finds an exponent `k` such that raising each implied probability to `k` yields a fair market (sum = 1.0):

```
Find k where: sum(implied_prob_i ^ k) = 1.0   (binary search)
true_prob_i = implied_prob_i ^ k / sum(implied_prob_j ^ k)
```

The power method is preferred because it better models how bookmakers load vig asymmetrically (longshots carry proportionally more vig than favorites).

**Example (multiplicative):**

| Side   | Implied   | True Probability        |
|--------|-----------|-------------------------|
| Team A | 0.5994    | 0.5994 / 1.0342 = 57.96% |
| Team B | 0.4348    | 0.4348 / 1.0342 = 42.04% |
| **Sum**|           | **100.00%**             |

### Step 4 — Calculate EV% and Quarter-Kelly Unit Size

#### Expected Value (EV%)

Compare the recreational book's offered odds against the true probability:

```
EV% = (true_probability × offered_decimal_odds) − 1.0
```

- **EV > 0:** The bet has positive expected value — the recreational book is offering odds that exceed the true fair price.
- **Threshold:** Only flag bets where `EV% > configured_threshold` (default: 2%).

**Example:** True probability of Team B = 42.04%. FanDuel offers Team B at +160 (decimal 2.60).

```
EV% = (0.4204 × 2.60) − 1.0 = 0.0930 = 9.30%
```

#### Quarter-Kelly Sizing

Full Kelly Criterion:

```
f* = (b × p − q) / b

where:
  b = decimal_odds − 1    (net payout per unit wagered)
  p = true_probability     (probability of winning)
  q = 1 − p                (probability of losing)
```

Quarter-Kelly divides the full Kelly fraction by 4 to reduce variance:

```
Quarter-Kelly% = (f* / 4) × 100
```

Capped at a maximum of 5% of bankroll per bet.

**Example:**

```
b = 2.60 − 1 = 1.60
f* = (1.60 × 0.4204 − 0.5796) / 1.60 = 0.0580
Quarter-Kelly = 0.0580 / 4 × 100 = 1.45% of bankroll
```

---

## Execution & Storage

### Scheduling

The scanner supports two execution modes:

1. **One-shot:** `python hive_scanner.py` — runs a single scan cycle and exits.
2. **Continuous loop:** `python hive_scanner.py --loop --interval 300` — runs every N seconds (default 300 = 5 minutes).
3. **Cron job / GitHub Actions:** The existing `master_run.py` pipeline can also be triggered via GitHub Actions on a schedule.

### Rate-Limit Handling

- The Odds API has per-key credit limits. Each sport × region call costs credits.
- The scanner logs remaining credits from the `x-requests-remaining` response header after each call.
- Configure `HIVE_SPORTS` and `HIVE_MARKETS` to limit the scan scope and conserve credits.
- Use tiered API keys (`ODDS_API_KEY`, `ODDS_API_KEY_2`, `ODDS_API_KEY_3`) for expanded coverage via `master_odds_fetcher.py`.

### Caching & Deduplication

To prevent duplicate Discord webhook spam:

- **Local JSON cache** (`hive_alert_cache.json`): Each alert is hashed by `matchup|market|selection|book`. If the hash exists in the cache, the alert is suppressed.
- **TTL:** Cache entries expire after a configurable window (default 12 hours), controlled by `HIVE_CACHE_TTL_HOURS`.
- **Supabase (production):** The `unified_bot.py` pipeline uses Supabase's `bets_log` table for persistent deduplication (`is_already_logged` checks matchup + market + selection).

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ODDS_API_KEY` | Yes | — | The Odds API key |
| `DISCORD_WEBHOOK_URL` | Yes | — | Discord webhook for alerts |
| `HIVE_EV_THRESHOLD` | No | `0.02` | Minimum EV% to fire alert (decimal) |
| `HIVE_SPORTS` | No | `basketball_nba,baseball_mlb,icehockey_nhl,americanfootball_nfl,basketball_wnba` | Comma-separated sport keys |
| `HIVE_MARKETS` | No | `h2h,spreads,totals` | Markets to scan |
| `HIVE_SOFT_BOOKS` | No | `draftkings,fanduel,betmgm,caesars,bet365` | Recreational books |
| `HIVE_DEVIG_METHOD` | No | `power` | De-vig method: `power` or `multiplicative` |
| `HIVE_KELLY_CAP` | No | `5.0` | Max Quarter-Kelly % per bet |
| `HIVE_ALERT_CACHE` | No | `hive_alert_cache.json` | Dedup cache file path |
| `HIVE_CACHE_TTL_HOURS` | No | `12` | Hours before cache entries expire |

---

## Discord Alert Format

Each +EV alert is sent as a rich Discord embed:

- **Title:** "+EV Alert from The Hive"
- **Color:** Green (`#2ECC71`)
- **Body:**
  - Sport, Matchup, Market
  - Selection, Sportsbook, Offered Odds
  - Pinnacle Line, True Probability, Fair Value
  - EV%, Recommended bankroll % (Quarter-Kelly)
- **Footer:** "BEE BAKED BETS | The Hive +EV Scanner"
- **Timestamp:** UTC time of alert generation

---

## File Structure

| File | Purpose |
|------|---------|
| `hive_scanner.py` | Standalone +EV scanner (Pinnacle de-vig, Discord alerts, loop mode) |
| `unified_bot.py` | Full-featured scanner with model overlays, exposure tracking, book weights |
| `master_odds_fetcher.py` | Tiered API data ingestion engine |
| `master_run.py` | Central orchestration entry-point |
| `utils/odds.py` | Core math: de-vig, Kelly, odds conversion |
| `utils/kelly.py` | Bankroll-aware dynamic Kelly sizing |
| `db_manager.py` | Supabase + local JSON persistence |
| `services/alerts.py` | Discord alert dispatch |
| `services/http_client.py` | HTTP session with retries |
| `tests/test_hive_scanner.py` | 35 tests for hive_scanner math |
| `tests/test_odds.py` | Tests for utils/odds.py |
