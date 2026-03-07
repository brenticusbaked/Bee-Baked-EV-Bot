name: BetMGM Line Tracker

on:
  schedule:
    - cron: '10,40 * * * *'
  workflow_dispatch: 

permissions:
  contents: write  

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: true
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests
      - name: Run BetMGM Scraper
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
        run: python scraper-betmgm.py
      - name: Commit and Push MGM Log
        run: |
          git config --local user.email "github-actions[bot]@users.noreply.github.com"
          git config --local user.name "github-actions[bot]"
          if [ -f mgm_lines.json ]; then
            git add mgm_lines.json
            git commit -m "Update BetMGM lines" || echo "No changes"
            git pull --rebase origin main
            git push
          fi