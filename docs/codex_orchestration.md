# Codex Orchestration For The Syndicate

## GitHub Sync

Keep this workspace connected to the private GitHub repository. All strategy changes should happen on branches and be reviewed through pull requests.

Suggested branch names:

- `codex/backtest-<strategy>`
- `codex/threshold-tuning`
- `codex/venue-scoring`

## Project Variables

Store secrets as project/environment variables, never in repo files:

- `SUPABASE_URL`
- `SUPABASE_KEY` or read-only anon key for frontend clients
- `SUPABASE_DB_URL` for controlled SQL analysis jobs
- `ODDS_API_KEY` only in Supabase Edge Function secrets

Codex should prefer Supabase cached tables for analysis and should not directly call The Odds API for exploratory work.

## Data Analysis Subagent Pattern

Use the repo-local skill at:

```text
codex_skills/syndicate-quant-analysis/SKILL.md
```

Typical tasks:

- inspect CLV by source, market, sport, and book
- backtest threshold changes against `historical_odds`
- evaluate `venue_metrics` and update routing weights
- produce SQL migrations for missing analytics fields
- draft PRs with tests and rollback criteria

## Automated PR Loop

For each strategy idea:

1. Create a branch.
2. Add or update the backtest.
3. Run tests.
4. Summarize expected impact.
5. Open a draft PR.
6. Merge only after a paper-trading window confirms CLV improvement.

Recommended PR checklist:

- Data source is Supabase cached data.
- No new uncontrolled API calls.
- Tests cover parsing/scoring/filter behavior.
- Feature flag exists for experimental behavior.
- Rollback condition is explicit.

