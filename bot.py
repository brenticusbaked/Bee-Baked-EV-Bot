import os
import requests

# --- CONFIGURATION ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

# --- ODDS API PARAMETERS ---
SPORT = 'basketball_nba' # Now strictly targeting the NBA
REGIONS = 'us'
MARKETS = 'h2h,spreads,totals' # Scanning Moneyline, Spreads, and Over/Unders
BOOKMAKERS = 'fanduel,draftkings,betmgm' 
ODDS_FORMAT = 'decimal' 

def decimal_to_american(decimal_odds):
    if decimal_odds >= 2.0:
        return f"+{int((decimal_odds - 1) * 100)}"
    else:
        return str(int(-100 / (decimal_odds - 1)))

def get_arbitrage_opportunities():
    if not ODDS_API_KEY:
        print("Error: ODDS_API_KEY is missing.")
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
        print("Fetching NBA multi-market data from The Odds API...")
        response = requests.get(url, params=params, timeout=15)
        
        requests_remaining = response.headers.get('x-requests-remaining')
        print(f"Odds API Credits Remaining: {requests_remaining}")

        if response.status_code == 200:
            games_data = response.json()
            
            for game in games_data:
                matchup = f"{game['away_team']} @ {game['home_team']}"
                
                # We need to group our odds by market type AND the specific point line
                # Example key: 'totals_220.5' or 'spreads_5.5'
                market_groups = {}

                for bookmaker in game.get('bookmakers', []):
                    for market in bookmaker.get('markets', []):
                        m_key = market['key']
                        
                        for outcome in market['outcomes']:
                            team = outcome['name']
                            price = outcome['price']
                            point = outcome.get('point', '') # Blank for h2h
                            book_name = bookmaker['title']

                            # Create a unique group identifier so lines match perfectly
                            # For spreads, we use the absolute value so -5.5 and +5.5 group together
                            if m_key == 'spreads' and point != '':
                                group_id = f"{m_key}_{abs(float(point))}"
                            else:
                                group_id = f"{m_key}_{point}"

                            if group_id not in market_groups:
                                market_groups[group_id] = {}

                            # Track the best price for each side of this specific line
                            if team not in market_groups[group_id] or price > market_groups[group_id][team]['price']:
                                market_groups[group_id][team] = {'price': price, 'book': book_name, 'point': point}

                # Now check all our grouped lines for Arbs
                for group_id, best_odds in market_groups.items():
                    if len(best_odds) == 2:
                        teams = list(best_odds.keys())
                        team_a, team_b = teams[0], teams[1]

                        price_a = best_odds[team_a]['price']
                        price_b = best_odds[team_b]['price']

                        implied_prob_a = 1 / price_a
                        implied_prob_b = 1 / price_b
                        total_implied_prob = implied_prob_a + implied_prob_b

                        if total_implied_prob < 1.0:
                            profit_margin = (1.0 - total_implied_prob) * 100
                            
                            american_a = decimal_to_american(price_a)
                            american_b = decimal_to_american(price_b)
                            
                            # Format the point spread/total if it exists
                            point_a = f" {best_odds[team_a]['point']}" if best_odds[team_a]['point'] != '' else ""
                            point_b = f" {best_odds[team_b]['point']}" if best_odds[team_b]['point'] != '' else ""

                            # Determine the market label for Discord
                            market_label = group_id.split('_')[0].upper()

                            formatted_msg = (
                                f"**🚨 NBA +EV ARBITRAGE ALERT ({profit_margin:.2f}% Margin) 🚨**\n"
                                f"**Game:** {matchup}\n"
                                f"**Market:** {market_label}\n"
                                f"**Leg 1:** {team_a}{point_a} ({american_a}) via {best_odds[team_a]['book']}\n"
                                f"**Leg 2:** {team_b}{point_b} ({american_b}) via {best_odds[team_b]['book']}\n\n"
                                f"👉 *Lock these in fast!*"
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
        return
    payload = {"content": message_content}
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

def main():
    print("Starting $BEE BAKED BETS NBA Multi-Market Scan...")
    
    arb_picks = get_arbitrage_opportunities()
    
    if arb_picks:
        for pick in arb_picks:
            send_to_discord(pick)
            print("Arb alert sent to Discord!")
    else:
        no_arb_msg = "🏀 **$BEE BAKED NBA Scan Complete:** No Moneyline, Spread, or Total arbitrage opportunities found across FD, DK, and BetMGM right now. Bankroll protected. 🛡️"
        send_to_discord(no_arb_msg)
        print("No +EV arbitrage opportunities found right now. Discord notified.")

if __name__ == "__main__":
    main()