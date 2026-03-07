import os
import requests

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# --- ODDS API PARAMETERS ---
SPORT = 'basketball_nba' 
REGIONS = 'us,eu' # Pinnacle is often in the 'eu' region on the API
MARKETS = 'h2h,spreads,totals'
# We added Pinnacle as our sharp book for the math
BOOKMAKERS = 'fanduel,draftkings,betmgm,pinnacle' 
ODDS_FORMAT = 'decimal' 

def decimal_to_american(decimal_odds):
    if decimal_odds >= 2.0:
        return f"+{int((decimal_odds - 1) * 100)}"
    else:
        return str(int(-100 / (decimal_odds - 1)))

def get_ev_bets():
    if not ODDS_API_KEY:
        print("❌ Error: ODDS_API_KEY is missing.")
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
        print("Fetching data from The Odds API to calculate Expected Value...")
        response = requests.get(url, params=params, timeout=15)
        
        requests_remaining = response.headers.get('x-requests-remaining')
        print(f"Odds API Credits Remaining: {requests_remaining}")

        if response.status_code == 200:
            games_data = response.json()
            
            for game in games_data:
                matchup = f"{game['away_team']} @ {game['home_team']}"
                market_groups = {}

                # 1. Group the lines together just like we did for Arbitrage
                for bookmaker in game.get('bookmakers', []):
                    book_name = bookmaker['key'] # Using 'key' to easily identify pinnacle
                    display_name = bookmaker['title']

                    for market in bookmaker.get('markets', []):
                        m_key = market['key']
                        
                        for outcome in market['outcomes']:
                            team = outcome['name']
                            price = outcome['price']
                            point = outcome.get('point', '')

                            if m_key == 'spreads' and point != '':
                                group_id = f"{m_key}_{abs(float(point))}"
                            else:
                                group_id = f"{m_key}_{point}"

                            if group_id not in market_groups:
                                market_groups[group_id] = {'pinnacle': {}, 'soft_books': {}}

                            # Separate Pinnacle (our Sharp book) from the Soft books
                            if book_name == 'pinnacle':
                                market_groups[group_id]['pinnacle'][team] = price
                            else:
                                if team not in market_groups[group_id]['soft_books'] or price > market_groups[group_id]['soft_books'][team]['price']:
                                    market_groups[group_id]['soft_books'][team] = {'price': price, 'book': display_name, 'point': point}

                # 2. Calculate +EV for each grouped line
                for group_id, data in market_groups.items():
                    sharp_data = data['pinnacle']
                    soft_data = data['soft_books']

                    # We can only calculate True Probability if Pinnacle has odds for BOTH sides of the exact line
                    if len(sharp_data) == 2:
                        teams = list(sharp_data.keys())
                        team_a, team_b = teams[0], teams[1]

                        price_pin_a = sharp_data[team_a]
                        price_pin_b = sharp_data[team_b]

                        # Calculate Pinnacle's Implied Probabilities and Vig
                        implied_a = 1 / price_pin_a
                        implied_b = 1 / price_pin_b
                        vig = implied_a + implied_b

                        # Remove the Vig to get the "Fair" or "True" Probability
                        fair_prob_a = implied_a / vig
                        fair_prob_b = implied_b / vig

                        # 3. Check the soft books to see if their odds beat the Fair Probability
                        for team, fair_prob in [(team_a, fair_prob_a), (team_b, fair_prob_b)]:
                            if team in soft_data:
                                soft_price = soft_data[team]['price']
                                
                                # Expected Value Formula: (Decimal Odds * True Probability) - 1
                                expected_value = (soft_price * fair_prob) - 1
                                ev_percentage = expected_value * 100

                                # Let's only alert if the +EV is greater than 1.0% to filter out tiny edges
                                if ev_percentage > 1.0:
                                    american_odds = decimal