import os
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

def scrape_kambi(url):
    """
    Reads Kambi-powered odds directly from the page DOM.
    Works for BetRivers, Unibet, etc.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        print(f"Scanning Kambi lines at {url}...")
        page.goto(url, wait_until="domcontentloaded")
        
        # Wait for the odds table to appear
        page.wait_for_selector(".KambiBC-event-item__event-wrapper", timeout=15000)
        
        events = page.query_selector_all(".KambiBC-event-item__event-wrapper")
        scraped_data = []

        for event in events:
            try:
                # Extracting Matchup
                participants = event.query_selector_all(".KambiBC-event-participants__name")
                if len(participants) < 2: continue
                matchup = f"{participants[0].inner_text()} @ {participants[1].inner_text()}"
                
                # Extracting Moneyline Odds
                odds_buttons = event.query_selector_all(".KambiBC-mod-event-outcome__odds")
                if len(odds_buttons) >= 2:
                    away_odds = odds_buttons[0].inner_text()
                    home_odds = odds_buttons[1].inner_text()
                    
                    scraped_data.append({
                        "Matchup": matchup,
                        "Away_ML": away_odds,
                        "Home_ML": home_odds,
                        "Timestamp": datetime.now().isoformat()
                    })
            except Exception as e:
                print(f"Skip row error: {e}")

        browser.close()
        return scraped_data

if __name__ == "__main__":
    # Example for BetRivers Illinois
    betrivers_nba = "https://il.betrivers.com/?page=sportsbook#filter/basketball/nba"
    data = scrape_k