import os
import requests

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# --- ODDS API PARAMETERS ---
SPORT = 'basketball_nba' 
REGIONS = 'us,eu' 
MARKETS = 'h2h,spreads,totals'
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

                for bookmaker in game.get('bookmakers', []):
                    book_name = bookmaker['key'] 
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

                            if book_name == 'pinnacle':
                                market_groups[group_id]['pinnacle'][team] = price
                            else:
                                if team not in market_groups[group_id]['soft_books'] or price > market_groups[group_id]['soft_books'][team]['price']:
                                    market_groups[group_id]['soft_books'][team] = {'price': price, 'book': display_name, 'point': point}

                for group_id, data in market_groups.items():
                    sharp_data = data['pinnacle']
                    soft_data = data['soft_books']

                    if len(sharp_data) == 2:
                        teams = list(sharp_data.keys())
                        team_a, team_b = teams[0], teams[1]

                        price_pin_a = sharp_data[team_a]
                        price_pin_b = sharp_data[team_b]

                        implied_a = 1 / price_pin_a
                        implied_b = 1 / price_pin_b
                        vig = implied_a + implied_b

                        fair_prob_a = implied_a / vig
                        fair_prob_b = implied_b / vig

                        for team, fair_prob in [(team_a, fair_prob_a), (team_b, fair_prob_b)]:
                            if team in soft_data:
                                soft_price = soft_data[team]['price']
                                
                                expected_value = (soft_price * fair_prob) - 1
                                ev_percentage = expected_value * 100

                                if ev_percentage > 1.0:
                                    american_odds = decimal_to_american(soft_price)
                                    book = soft_data[team]['book']
                                    point_str = f" {soft_data[team]['point']}" if soft_data[team]['point'] != '' else ""
                                    market_label = group_id.split('_')[0].upper()

                                    formatted_msg