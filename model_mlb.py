import os

from db_manager import get_master_cache, is_already_logged, log_bet_to_db, load_tracker_state
from services.alerts import send_discord_alert
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from services.http_client import get_json
from utils.links import sportsbook_search_link
from utils.model_pricing import fair_american_from_probability, model_edge_from_probability
from utils.odds import decimal_to_american, quarter_kelly_units, american_to_decimal
from utils.thresholds import env_float
from utils.time import get_local_now


DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
MLB_FIP_GAP_THRESHOLD = env_float("MLB_FIP_GAP_THRESHOLD", 1.25)
MLB_MODEL_EDGE_THRESHOLD = env_float("MLB_MODEL_EDGE_THRESHOLD", 0.01)
F5_MARKET_PRIORITY = {"h2h_1st_5_innings": 2, "h2h_1st_half": 2, "h2h": 1}


def get_dynamic_link(bookmaker, target_string):
    return sportsbook_search_link(bookmaker, target_string)


def _team_aliases(team_name: str):
    normalized = str(team_name).lower().replace(".", "").strip()
    aliases = {normalized}
    replacements = {
        "arizona diamondbacks": {"arizona dbacks", "dbacks", "diamondbacks"},
        "athletics": {"oakland athletics", "athletics"},
        "chicago white sox": {"white sox"},
        "boston red sox": {"red sox"},
        "los angeles angels": {"la angels", "angels"},
        "los angeles dodgers": {"la dodgers", "dodgers"},
        "new york mets": {"ny mets", "mets"},
        "new york yankees": {"ny yankees", "yankees"},
        "san francisco giants": {"sf giants", "giants"},
        "st louis cardinals": {"st louis cardinals", "cardinals"},
        "tampa bay rays": {"tb rays", "rays"},
    }
    aliases.update(replacements.get(normalized, set()))
    words = normalized.split()
    if words:
        aliases.add(words[-1])
    return {alias for alias in aliases if alias}


def _team_matches(target_team: str, candidate: str) -> bool:
    candidate_text = str(candidate).lower().replace(".", "").strip()
    if not candidate_text:
        return False
    return any(alias == candidate_text or alias in candidate_text for alias in _team_aliases(target_team))


def get_best_f5_moneyline(target_team):
    cache = get_master_cache()
    if not cache:
        return None, None, None, None, None

    best_price = 0.0
    best_book = "Unknown"
    best_book_title = "Unknown"
    event_id = None
    selected_market = None
    selected_priority = 0

    for game in cache.get("baseball_mlb", []):
        if not _team_matches(target_team, game["home_team"]) and not _team_matches(target_team, game["away_team"]):
            continue
        for bookmaker in game.get("bookmakers", []):
            if bookmaker["key"] == "pinnacle":
                continue
            for market in bookmaker.get("markets", []):
                market_priority = F5_MARKET_PRIORITY.get(market["key"], 0)
                if not market_priority:
                    continue
                for outcome in market["outcomes"]:
                    if _team_matches(target_team, outcome["name"]):
                        price = float(outcome["price"])
                        if market_priority > selected_priority or (
                            market_priority == selected_priority and price > best_price
                        ):
                            best_price = price
                            best_book = bookmaker["key"]
                            best_book_title = bookmaker["title"]
                            event_id = game["id"]
                            selected_market = market["key"]
                            selected_priority = market_priority

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
    
    str_id = str(pitcher_id)
    source = "statsapi_estimate"
    if fip_cache and str_id in fip_cache:
        actual_fip = fip_cache[str_id].get("fip")
        source = "fangraphs"
        
    try:
        person = get_json(url).get("people", [{}])[0]
        splits = person.get("stats", [{}])[0].get("splits", [{}])
        if splits:
            stats = splits[0].get("stat", {})
            k9 = float(stats.get("strikeOutsPer9Inn", 0))
            bb9 = float(stats.get("walksPer9Inn", 0))
            hr9 = float(stats.get("homeRunsPer9", 0))
            era = float(stats.get("era", 9.99))
            est_fip = ((13 * hr9) + (3 * bb9) - (2 * k9)) / 9 + 3.20
    except Exception as exc:
        print(f"Error fetching stats for Pitcher ID {pitcher_id}: {exc}")
        
    api_cache[pitcher_id] = (est_fip, actual_fip, era, source)
    return api_cache[pitcher_id]


def run_mlb_model():
    today = get_local_now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=probablePitcher"

    try:
        data = get_json(url)
        dates = data.get("dates", [])
        if not dates:
            return {"detail": "no mlb games scheduled", "count": 0, "label": "alerts"}

        pybaseball_cache = load_tracker_state("mlb_fip_cache", "fip_cache.json") or {}
        alerts = []
        pitcher_stats_cache = {}

        for game in dates[0].get("games", []):
            away_team = game["teams"]["away"]["team"]["name"]
            home_team = game["teams"]["home"]["team"]["name"]
            matchup = f"{away_team} @ {home_team}"

            away_p = game["teams"]["away"].get("probablePitcher")
            home_p = game["teams"]["home"].get("probablePitcher")
            if not away_p or not home_p: continue

            a_est_fip, a_act_fip, a_era, a_source = get_advanced_pitcher_stats(away_p["id"], pitcher_stats_cache, pybaseball_cache)
            h_est_fip, h_act_fip, h_era, h_source = get_advanced_pitcher_stats(home_p["id"], pitcher_stats_cache, pybaseball_cache)
            
            a_mod_fip = a_act_fip if a_act_fip is not None else a_est_fip
            h_mod_fip = h_act_fip if h_act_fip is not None else h_est_fip
            if a_mod_fip is None or h_mod_fip is None: continue

            fip_diff = abs(a_mod_fip - h_mod_fip)
            if fip_diff < MLB_FIP_GAP_THRESHOLD: continue

            if a_mod_fip < h_mod_fip:
                better_team, adv_p, disadv_p = away_team, away_p['fullName'], home_p['fullName']
            else:
                better_team, adv_p, disadv_p = home_team, home_p['fullName'], away_p['fullName']

            if is_already_logged(matchup, "MODEL_MLB_F5", better_team): continue

            book, odds, link, event_id, selected_market = get_best_f5_moneyline(better_team)
            if not book or not event_id: continue

            # Core Model Probabilities
            prob = min(0.53 + max(fip_diff - MLB_FIP_GAP_THRESHOLD, 0.0) * 0.03, 0.64)
            fair_p = fair_american_from_probability(prob)
            edge = model_edge_from_probability(prob, odds)
            if edge < MLB_MODEL_EDGE_THRESHOLD:
                continue
            
            # Use utility for precise Quarter-Kelly sizing
            dec_odds = american_to_decimal(odds)
            u_size = quarter_kelly_units(edge, dec_odds)
            if u_size <= 0:
                continue

            was_logged = log_bet_to_db(
                matchup,
                "MODEL_MLB_F5",
                better_team,
                odds,
                edge,
                f"{u_size:.2f}",
                fair_p,
                "baseball_mlb",
                event_id,
                notes=(
                    f"book={book};market={selected_market};model=mlb_fip;"
                    f"probability={prob:.4f};fip_diff={fip_diff:.4f};"
                    f"away_source={a_source};home_source={h_source}"
                ),
            )
            if not was_logged:
                print(f"Skipping MLB model alert because DB log failed for {better_team}.")
                continue

            # Dynamic Angle Sizing (Estimating secondary edges as ~75% of primary F5 edge)
            secondary_u = max(0.5, round(u_size * 0.75, 1))

            angles_text = (
                f"**🎯 Suggested Angles to Shop:**\n"
                f"• **F5 ML or -0.5:** {better_team} ({u_size:.1f}u)\n"
                f"• **Team Total:** {better_team} OVER ({secondary_u}u)\n"
                f"• **Strikeout Props:** {adv_p} OVER ({secondary_u}u)\n"
                f"• **Earned Runs:** {disadv_p} OVER ({secondary_u}u)"
            )

            alerts.append(
                (
                    f"**MLB ADVANCED METRIC MISMATCH**\n"
                    f"**Game:** {matchup}\n"
                    f"**Advantage:** {better_team} (F5)\n"
                    f"**{away_p['fullName']}** FIP: **{a_mod_fip:.2f}** | ERA: **{a_era:.2f}** ({a_source})\n"
                    f"**{home_p['fullName']}** FIP: **{h_mod_fip:.2f}** | ERA: **{h_era:.2f}** ({h_source})\n"
                    f"**FIP Gap:** {fip_diff:.2f}\n"
                    f"**Price:** [{book}]({link}) @ {odds}\n"
                    f"**Model Edge:** {edge * 100:.2f}% | **Fair:** {fair_p}\n"
                    f"**Primary Bet:** {u_size:.2f} Units\n\n"
                    f"{angles_text}"
                )
            )

        for index, message in enumerate(alerts):
            send_discord_alert({"embeds": [{"description": message, "color": 3066993}]}, "model_mlb", "bet_alert", message[:200], DISCORD_WEBHOOK_URL, index == len(alerts)-1)
        return {"detail": "mlb model complete", "count": len(alerts), "label": "alerts"}
    except Exception as exc:
        return {"detail": f"error: {exc}", "count": 0, "label": "alerts"}

if __name__ == "__main__":
    run_mlb_model()
