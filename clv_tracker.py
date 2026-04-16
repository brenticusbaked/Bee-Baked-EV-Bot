name: Syndicate Automation

on:
  schedule:
    # Set to your preferred times (currently 8 AM and 4 PM UTC)
    - cron: '0 8,16 * * *'
  workflow_dispatch:

jobs:
  run-syndicate:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      # SPEED OPTIMIZATION: Reuses browsers if dependencies haven't changed
      - name: Cache Playwright Browsers
        id: cache-playwright
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: ${{ runner.os }}-playwright-${{ hashFiles('requirements.txt') }}

      - name: Install Dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      # CRITICAL FIX: Ensures Chromium and Linux deps are present for scrapers
      - name: Install Playwright Chromium
        if: steps.cache-playwright.outputs.cache-hit != 'true'
        run: npx playwright install --with-deps chromium

      - name: Run Syndicate Master
        env:
          ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
          SGO_API_KEY: ${{ secrets.SGO_API_KEY }}
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
        run: python master_run.py