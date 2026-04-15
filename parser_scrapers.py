import json
import os
from datetime import datetime
from db_manager import log_bet_to_db

def log_parsed_bet(matchup, market, selection, odds):
    """Standardized logger for scraper data sending directly to Supabase."""
    log_bet_to_db(
        matchup=matchup,
        market=market,
        selection=selection,
        odds=odds,
        edge_val="SCRAPED", # Placeholder for Edge
        units="1.00",       # Default Unit
        fair_price="N/A"    # No Fair Price available from raw scrape
    )

def parse_bovada_json(filepath):
    """Parses Bovada raw network capture into the database."""
    if not os.path.exists(filepath): return
    with open(filepath, 'r') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            print("Error: JSON file is corrupted or empty.")
            return
            
        if isinstance(data, list):
            for event_group in data:
                if isinstance(event_group, dict):
                    for event in event_group.get('path', [{}])[0].get('events', []):
                        matchup = event.get('description')
                        for display_group in event.get('displayGroups', []):
                            if display_group.get('description') == 'Game Lines':
                                for market in display_group.get('markets', []):
                                    if market.get('description') == 'Moneyline':
                                        for outcome in market.get('outcomes', []):
                                            selection = outcome.get('description')
                                            price = outcome.get('price', {}).get('american')
                                            
                                            if price: 
                                                log_parsed_bet(matchup, "MONEYLINE", f"Bovada: {selection}", price)
        else:
            print("Bovada data was blocked or returned unexpected format.")

if __name__ == "__main__":
    print("Parsing raw scraper data...")
    parse_bovada_json("bovada_nba_raw.json")
    print("Log updated.")