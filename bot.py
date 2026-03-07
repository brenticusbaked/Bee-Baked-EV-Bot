import os
import requests

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# --- ODDS API PARAMETERS ---
SPORT = 'upcoming' 
REGIONS = 'us'
MARKETS = 'h2h'
# We are now pulling from TWO books to find discrepancies
BOOKMAKERS = 'fanduel,draftkings' 
# We request decimal odds because it makes the math much easier
ODDS_FORMAT = 'decimal' 

def decimal_to_american(decimal_odds):
    """Converts decimal odds to American formatting for Discord."""
    if decimal_odds >= 2.0:
        return f"+{int((decimal_odds - 1) * 100)}"
    else:
        return str(int(-100 / (decimal_odds - 1)))

def get_arbitrage_opportunities():
    """
    Fetches odds from FD and DK, calculates implied probabilities,
    and returns a list of +EV/Arbitrage alerts.
    """
    if not ODDS_API_KEY:
        print("Error: ODDS_API_KEY is missing. Add it to GitHub Secrets.")
        return []

    picks_list = []
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
    
    params = {
        'apiKey': ODDS_API_KEY,
        'regions': REGIONS,
        'markets': MARKETS,
        'bookmakers': BOOKMAKERS,
        'oddsFormat': ODDS_FORMAT
    }

    try:
        print("Fetching dual-book data from The Odds API...")
        response = requests.get(url, params=params, timeout=15)
        
        # Monitor those credits!
        requests_remaining = response.headers.get('x-requests-remaining')
        print(f"Odds API Credits Remaining: {requests_remaining}")

        if response.status_code == 200:
            games_data = response.json()
            
            for game in games_data:
                matchup = f"{game['away_team']} @ {game['home_team']}"
                best_odds = {}

                # 1. Find the best odds for each team across our selected books
                for bookmaker in game.get('bookmakers', []):
                    for market in bookmaker.get('markets', []):
                        if market['key'] == 'h2h':
                            for outcome in market['outcomes']:
                                team = outcome['name']
                                price = outcome['price']
                                book_name = bookmaker['title']

                                # If we haven't tracked this team yet, or we found a better price
                                if team not in best_odds or price > best_odds[team]['price']:
                                    best_odds[team] = {'price': price, 'book': book_name}

                # 2. We need exactly two teams to calculate H2H arbitrage
                if len(best_odds) == 2:
                    teams = list(best_odds.keys())
                    team_a, team_b = teams[0], teams[1]

                    price_a = best