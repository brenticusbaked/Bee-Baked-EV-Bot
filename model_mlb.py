import os

from db_manager import get_master_cache, is_already_logged, log_bet_to_db, load_tracker_state
from services.alerts import send_discord_alert
from services.http_client import get_json
from utils.links import sportsbook_search_link
from utils.model_pricing import fair_american_from_probability, model_edge_from_probability, model_units_from_probability
from utils.odds import decimal_to_american, quarter_kelly_units
from utils.thresholds import env_float
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
MLB_FIP_GAP_THRESHOLD = env_float("MLB_FIP_GAP_THRESHOLD", 1.25)


def get_dynamic_link(bookmaker, target_string):
    return sportsbook_search_link(bookmaker, target_string)


def get_best_f5_moneyline(target_team):
    cache = get_master_cache()
    if not cache:
        print("Cloud cache is empty or failed to load.")
        return None, None, None, None, None

    best_price = 0.0
    best_book = "Unknown"
    best_book_title = "Unknown"
    event_id = None
    selected_market = None

    for game in cache.get("baseball_mlb", []):
        if target_team not in game["home_team"] and target_team not in game["away_team"]:
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker["key"] == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                if market["key"] not in {"h2h_1st_half", "h2h"}:
                    continue
                for outcome in market["outcomes"]:
                    if target_team in outcome["name"]:
                        price = float(outcome["price"])
                        if price > best_price:
                            best_price = price
                            best_book = bookmaker["key"]
                            best_book_title = bookmaker["title"]
                            event_id = game["id"]
                            selected_market = market["key"]

    if best_price > 0:
        return (
            best_book_title,
            decimal_to_american(best_price),
            get_dynamic_link(best_book, target_team),
            event_id,
            selected_market,
        )
    return None, None, None, None, None


def get_advanced_pitcher_stats(pitcher_id, api_cache, fip_cache):
    if pitcher_id in api_cache:
        return api_cache[pitcher_id]
        
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}?hydrate=stats(group=[pitching],type=[season])"
    
    est_fip = None
    era = 9.99
    actual_fip = None
    
    # Grab True FIP from the pybaseball scraper cache
    str_id = str(pitcher_id)
    if fip_cache and str_id in fip_cache:
        actual_fip = fip_cache[str_id].get("fip")
        
    # Grab Estimated FIP and ERA from MLB Stats API
    try:
        person = get_json(url).get("people", [{}])[0]
        splits = person.get("stats", [{}])[0].get("splits", [{}])
        if splits:
            stats = splits[0].get("stat", {})
            k9 = float(stats.get("strikeOutsPer9Inn", 0))
            bb9 = float(stats.get("walksPer9Inn", 0))
            hr9 = float(stats.get("homeRunsPer9", 0))
            era = float(stats.get("era", 9.99))
            
            # Static formula
            est_fip = ((13 * hr9) + (3 * bb9) - (2 * k9)) / 9 + 3.20
    except Exception as exc:
        print(f"Error fetching stats for Pitcher ID {pitcher_id}: {exc}")
        
    api_cache[pitcher_id] = (est_fip, actual_fip, era)
    return api_cache[pitcher_id]


def run_mlb_model():
    today = get_local_now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"

    try:
        data = get_json(url)
        dates = data.get("dates", [])
        if not dates:
            print(f"No MLB games scheduled for {today}.")
            return {"detail": "no mlb games scheduled", "count": 0, "label": "alerts"}

        # Load the FanGraphs/Pybaseball true FIP cache
        pybaseball_cache = load_tracker_state("mlb_fip_cache", "fip_cache.json") or {}

        alerts = []
        pitcher_stats_cache = {}
        for game in dates[0].get("games", []):
            away_team = game["teams"]["away"]["team"]["name"]
            home_team = game["teams"]["home"]["team"]["name"]
            matchup = f"{away_team} @ {home_team}"

            away_pitcher = game["teams"]["away"].get("probablePitcher")
            home_pitcher = game["teams"]["home"].get("probablePitcher")
            if not away_pitcher or not home_pitcher:
                continue

            away_est_fip, away_act_fip, away_era = get_advanced_pitcher_stats(away_pitcher["id"], pitcher_stats_cache, pybaseball_cache)
            home_est_fip, home_act_fip, home_era = get_advanced_pitcher_stats(home_pitcher["id"], pitcher_stats_cache, pybaseball_cache)
            
            # Prioritize True FIP for the model logic, fallback to Estimated FIP
            away_model_fip = away_act_fip if away_act_fip is not None else away_est_fip
            home_model_fip = home_act_fip if home_act_fip is not None else home_est_fip
            
            if away_model_fip is None or home_model_fip is None:
                continue

            fip_diff = abs(away_model_fip - home_model_fip)
            if fip_diff < MLB_FIP_GAP_THRESHOLD:
                continue

            # Determine the advantage side for prop suggestions
            if away_model_fip < home_model_fip:
                better_team = away_team
                adv_pitcher_name = away_pitcher['fullName']
                disadv_pitcher_name = home_pitcher['fullName']
            else:
                better_team = home_team
                adv_pitcher_name = home_pitcher['fullName']
                disadv_pitcher_name = away_pitcher['fullName']

            if is_already_logged(matchup, "MODEL_MLB_F5", better_team):
                continue

            book, odds, link, event_id, selected_market = get_best_f5_moneyline(better_team)
            if not book or not event_id:
                continue

            model_probability = min(0.53 + max(fip_diff - MLB_FIP_GAP_THRESHOLD, 0.0) * 0.03, 0.64)
            fair_price = fair_american_from_probability(model_probability)
            edge = model_edge_from_probability(model_probability, odds)
            units = model_units_from_probability(model_probability, odds)

            was_logged = log_bet_to_db(
                matchup,
                "MODEL_MLB_F5",
                better_team,
                odds,
                edge,
                f"{units:.2f}",
                fair_price,
                "baseball_mlb",
                event_id,
                notes=f"book={book};market={selected_market};model=mlb_fip;probability={model_probability:.4f};fip_diff={fip_diff:.4f}",
            )
            if not was_logged:
                print(f"Skipping MLB model alert because DB log failed for {better_team}.")
                continue
                
            # Formatting helpers for the Discord message
            away_e_str = f"{away_est_fip:.2f}" if away_est_fip else "N/A"
            away_a_str = f"{away_act_fip:.2f}" if away_act_fip else "N/A"
            home_e_str = f"{home_est_fip:.2f}" if home_est_fip else "N/A"
            home_a_str = f"{home_act_fip:.2f}" if home_act_fip else "N/A"
            
            market_label = "Best F5 ML" if selected_market == "h2h_1st_half" else "Best ML"
            
            # Dynamic Angle Suggestions
            angles_text = (
                f"**🎯 Suggested Angles to Shop:**\n"
                f"• **First 5 Innings:** {better_team} ML or -0.5\n"
                f"• **Opponent Team Total:** {better_team} OVER\n"
                f"• **Strikeout Props:** {adv_pitcher_name} OVER\n"
                f"• **Earned Runs Allowed:** {disadv_pitcher_name} OVER"
            )

            alerts.append(
                (
                    f"**MLB ADVANCED METRIC MISMATCH**\n"
                    f"**Game:** {matchup}\n"
                    f"**Advantage:** {better_team} (First 5 Innings)\n"
                    f"{away_pitcher['fullName']} FIPs -> Est: {away_e_str} | True: **{away_a_str}** (ERA: {away_era:.2f})\n"
                    f"{home_pitcher['fullName']} FIPs -> Est: {home_e_str} | True: **{home_a_str}** (ERA: {home_era:.2f})\n"
                    f"**{market_label}:** [{book}]({link}) @ {odds}\n"
                    f"**Fair Value:** {fair_price}\n"
                    f"**Model Edge:** {edge * 100:.2f}%\n"
                    f"**Suggested:** {units:.2f} Units\n\n"
                    f"{angles_text}"
                )
            )

        for index, message in enumerate(alerts):
            send_discord_alert(
                {"embeds": [{"description": message, "color": 3066993}]},
                source="model_mlb",
                alert_type="bet_alert",
                dedupe_key=message[:200],
                webhook_url=DISCORD_WEBHOOK_URL,
                add_bee_image=index == len(alerts) - 1,
            )
        return {"detail": "mlb model complete", "count": len(alerts), "label": "alerts"}
    except Exception as exc:
        print(f"Error running MLB model: {exc}")
        return {"detail": f"mlb model error: {exc}", "count": 0, "label": "alerts"}


if __name__ == "__main__":
    run_mlb_model()