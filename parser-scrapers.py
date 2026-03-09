import json
import csv
import os
from datetime import datetime

def log_parsed_bet(matchup, market, selection, odds):
    """Standardized 10-column logger for scraper data."""
    file_exists = os.path.isfile('bets_log.csv')
    with open('bets_log.csv', mode='a', newline='') as f:
        writer = csv.writer(f)
        if not file_exists:
            # Matches the exact structure used by your Unified Bot and Models
            writer.writerow(['Date', 'Matchup', 'Market', 'Selection', 'Odds', 'Edge', 'Units', 'FairPriceAtBet', 'Closing_Line_Pinnacle', 'Result'])
        
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d"),
            matchup,
            market,
            selection,
            odds,
            "SCRAPED", # Placeholder for Edge
            "1.00",    # Default Unit
            "N/A",     # No Fair Price available from raw scrape
            "",        # Empty Closing Line for clv-tracker to fill later
            ""         # Empty Result column
        ])

def parse_bovada_json(filepath):
    """Parses Bovada raw network capture into the CSV log."""
    if not os.path.exists(filepath): return
    with open(filepath, 'r') as f:
        data = json.load(f)
        for event_group in data:
            for event in event_group.get('path', [{}])[0].get('events', []):
                matchup = event.get('description')
                for display_group in event.get('displayGroups', []):
                    if display_group.get('description') == 'Game Lines':
                        for market in display_group.get('markets', []):
                            if market.get('description') == 'Moneyline':
                                for outcome in market.get('outcomes', []):
                                    selection = outcome.get('description')
                                    price = outcome.get('price', {}).get('american')
                                    log_parsed_bet(matchup, "MONEYLINE", f"Bovada: {selection}", price)

if __name__ == "__main__":
    print("Parsing raw scraper data...")
    parse_bovada_json("bovada_nba_raw.json")
    print("Log updated.")