import os
import requests

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# --- ODDS API PARAMETERS ---
SPORT = 'basketball_nba' 
REGIONS = 'us,eu' 
# The 4 Sharpest Markets based on Pinnacle's liquidity
MARKETS = 'h2h,spreads,totals,player_points'
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
        print("Fetching the 4 Sharpest Markets (ML, Spreads, Totals, Points)...")
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
                            if 'description' in outcome:
                                team = f"{outcome['description']} {outcome['name']}"
                            else:
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

                        for team, fair_prob, pin_price in [(team_a, fair_prob_a, price_pin_a), (team_b, fair_prob_b, price_pin_b)]:
                            if team in soft_data:
                                soft_price = soft_data[team]['price']
                                
                                expected_value = (soft_price * fair_prob) - 1
                                ev_percentage = expected_value * 100

                                if ev_percentage > 1.0: 
                                    b = soft_price - 1
                                    kelly_fraction = expected_value / b
                                    
                                    quarter_kelly_units = (kelly_fraction / 4) * 100
                                    
                                    if quarter_kelly_units > 5.0:
                                        quarter_kelly_units = 5.0

                                    american_odds = decimal_to_american(soft_price)
                                    sharp_american = decimal_to_american(pin_price)
                                    book = soft_data[team]['book']
                                    point_str = f" {soft_data[team]['point']}" if soft_data[team]['point'] != '' else ""
                                    market_label = group_id.split('_')[0].upper()

                                    formatted_msg = (
                                        f"**💎 +EV VALUE ALERT ({ev_percentage:.2f}% Edge) 💎**\n"
                                        f"**Game:** {matchup}\n"
                                        f"**Market:** {market_label} | {team}{point_str}\n"
                                        f"**Best Book:** {book} @ {american_odds}\n"
                                        f"**Sharp Book:** Pinnacle @ {sharp_american} (True Prob: {(fair_prob * 100):.1f}%)\n"
                                        f"**Suggested Bet:** {quarter_kelly_units:.2f} Units (1/4 Kelly)\n\n"
                                        f"👉 *Lock it in on {book}!*"
                                    )
                                    picks_list.append(formatted_msg)

        elif response.status_code == 429:
            print("Credit limit reached on The Odds API.")
        else:
            print(f"API Error. Status: {response.status_code}")
            
    except Exception as e:
        print(f"Script Error: {e}")
        
    return picks_list

def send_to_discord(message_content):
    if not DISCORD_WEBHOOK_URL:
        print("❌ ERROR: DISCORD_WEBHOOK_URL is empty!")
        return False
        
    payload = {"content": message_content}
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if response.status_code == 204:
            return True
        else:
            print(f"❌ DISCORD REJECTED MESSAGE: Status Code {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ FAILED TO CONNECT TO DISCORD: {e}")
        return False

def main():
    print("Starting $BEE BAKED BETS Sharp Market Scanner...")
    
    ev_picks = get_ev_bets()
    
    if ev_picks:
        for pick in ev_picks:
            success = send_to_discord(pick)
            if success:
                print("✅ +EV alert successfully sent to Discord!")
    else:
        no_arb_msg = "🏀 **$BEE BAKED NBA Scan Complete:** No +EV Main Line or Point Prop edges > 1% found right now. Bankroll protected. 🛡️"
        success = send_to_discord(no_arb_msg)
        if success:
            print("✅ 'No EV' status successfully sent to Discord.")
        else:
            print("⚠️ Scan finished, but Discord notification failed.")

if __name__ == "__main__":
    main()