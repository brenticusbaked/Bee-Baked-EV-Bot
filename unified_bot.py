import os
from datetime import datetime, timezone

from db_manager import get_all_graded_bets, get_master_cache, get_today_bets, is_already_logged, log_bet_to_db
from models.nba_pace import PaceContext, build_pace_context, pace_total_adjustment
from models.nfl_proe import PROEContext, build_proe_context, proe_spread_adjustment, proe_total_adjustment
from models.nhl_pdo import PDOContext, build_pdo_context, pdo_total_adjustment
from models.talent_model import TalentContext, adjusted_fair_probability, build_talent_context
from services.alerts import send_discord_alert
from services.book_weights import get_book_weights
from services.discord_channels import BET_ALERTS_WEBHOOK_URL
from utils.correlation import ExposureEntry, ExposureTracker, check_exposure
from utils.kelly import dynamic_kelly_units
from utils.links import sportsbook_search_link
from utils.market_efficiency import score_market_efficiency
from utils.odds import (
    decimal_implied_probability,
    decimal_to_american,
    fair_probabilities_from_prices,
    multiplicative_unvig,
)
from utils.prop_pricing import prop_kelly_units
from utils.scratch_guard import filter_valid_events, validate_bookmaker_outcomes
from utils.thresholds import env_float, env_int
from utils.time_decay import adjusted_threshold, compute_time_decay


DISCORD_WEBHOOK_URL = BET_ALERTS_WEBHOOK_URL
SPORT_ALERT_WEBHOOKS = {
    "basketball_wnba": os.getenv("DISCORD_WNBA_BETS_WEBHOOK_URL") or DISCORD_WEBHOOK_URL,
    "baseball_mlb": os.getenv("DISCORD_MLB_BETS_WEBHOOK_URL") or DISCORD_WEBHOOK_URL,
    "icehockey_nhl": os.getenv("DISCORD_NHL_BETS_WEBHOOK_URL") or DISCORD_WEBHOOK_URL,
    "basketball_nba": os.getenv("DISCORD_NBA_BETS_WEBHOOK_URL") or DISCORD_WEBHOOK_URL,
    "americanfootball_nfl": os.getenv("DISCORD_NFL_BETS_WEBHOOK_URL") or DISCORD_WEBHOOK_URL,
}
UNIFIED_EV_THRESHOLD = env_float("UNIFIED_EV_THRESHOLD", 0.01)
UNIFIED_NEAR_MISS_THRESHOLD = env_float("UNIFIED_NEAR_MISS_THRESHOLD", 0.005)
UNIFIED_SPREAD_EV_THRESHOLD = env_float("UNIFIED_SPREAD_EV_THRESHOLD", UNIFIED_EV_THRESHOLD)
UNIFIED_H2H_EV_THRESHOLD = env_float("UNIFIED_H2H_EV_THRESHOLD", max(UNIFIED_EV_THRESHOLD, 0.02))
UNIFIED_TOTAL_EV_THRESHOLD = env_float("UNIFIED_TOTAL_EV_THRESHOLD", max(UNIFIED_EV_THRESHOLD, 0.015))
UNIFIED_ALT_MARKET_EV_THRESHOLD = env_float("UNIFIED_ALT_MARKET_EV_THRESHOLD", max(UNIFIED_EV_THRESHOLD, 0.02))
UNIFIED_PARTIAL_MARKET_EV_THRESHOLD = env_float("UNIFIED_PARTIAL_MARKET_EV_THRESHOLD", max(UNIFIED_EV_THRESHOLD, 0.02))
ENABLE_MLB_H2H_ALERTS = os.getenv("ENABLE_MLB_H2H_ALERTS", "false").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NBA_TOTAL_ALERTS = os.getenv("ENABLE_NBA_TOTAL_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NHL_TOTAL_ALERTS = os.getenv("ENABLE_NHL_TOTAL_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_MLB_SPREAD_ALERTS = os.getenv("ENABLE_MLB_SPREAD_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_MLB_TOTAL_ALERTS = os.getenv("ENABLE_MLB_TOTAL_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NFL_TOTAL_ALERTS = os.getenv("ENABLE_NFL_TOTAL_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NFL_SPREAD_ALERTS = os.getenv("ENABLE_NFL_SPREAD_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_NFL_H2H_ALERTS = os.getenv("ENABLE_NFL_H2H_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_ALTERNATE_MARKET_ALERTS = os.getenv("ENABLE_ALTERNATE_MARKET_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_PARTIAL_GAME_MARKET_ALERTS = os.getenv("ENABLE_PARTIAL_GAME_MARKET_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_PLAYER_PROP_ALERTS = os.getenv("ENABLE_PLAYER_PROP_ALERTS", "true").strip().lower() in {"1", "true", "yes", "on"}
UNIFIED_PROP_EV_THRESHOLD = env_float("UNIFIED_PROP_EV_THRESHOLD", max(UNIFIED_EV_THRESHOLD, 0.02))
# Player props carry highly asymmetric juice (e.g. Over -140 / Under +110), so
# they are de-vigged multiplicatively rather than with the power method used for
# main markets (see .windsurfrules Rule 1 / the syndicate spec).
PROP_DEVIG_METHOD = "multiplicative"
PROP_KELLY_FRACTION = env_float("UNIFIED_PROP_KELLY_FRACTION", 0.125)
PROP_MAX_UNITS = env_float("UNIFIED_PROP_MAX_UNITS", 2.0)
UNIFIED_MAX_ALERTS_PER_EVENT_MARKET = max(1, env_int("UNIFIED_MAX_ALERTS_PER_EVENT_MARKET", 3))
DEVIG_METHOD = os.getenv("DEVIG_METHOD", "power")
ENABLE_SHIN_DEVIG = os.getenv("ENABLE_SHIN_DEVIG", "").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_TIME_DECAY = os.getenv("ENABLE_TIME_DECAY", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_CORRELATION_LIMITS = os.getenv("ENABLE_CORRELATION_LIMITS", "true").strip().lower() in {"1", "true", "yes", "on"}
ENABLE_MARKET_EFFICIENCY = os.getenv("ENABLE_MARKET_EFFICIENCY", "true").strip().lower() in {"1", "true", "yes", "on"}
MIN_EFFICIENCY_SCORE = env_float("MIN_EFFICIENCY_SCORE", 0.30)


def get_mobile_app_link(book_key, selection_id, event_id, matchup):
    del selection_id, event_id
    return sportsbook_search_link(book_key, matchup)


def calculate_edge(offered_price: float, sharp_price: float) -> float:
    fair_probability = decimal_implied_probability(sharp_price)
    return (offered_price * fair_probability) - 1.0


def calculate_edge_from_probability(offered_price: float, fair_probability: float) -> float:
    return (float(offered_price) * float(fair_probability)) - 1.0


def _is_player_prop_market(market_type: str) -> bool:
    return str(market_type).strip().lower().startswith("player_")


def _market_family(market_type: str) -> str:
    market_key = str(market_type).strip().lower()
    if market_key.startswith("player_"):
        return "player_prop"
    if market_key.startswith("alternate_"):
        return "alternate"
    if any(token in market_key for token in ("_q1", "_1q", "_h1", "_1h", "_1st_5", "_first_5", "_1st_period", "_1p")):
        return "partial"
    if market_key.startswith("spreads"):
        return "spreads"
    if market_key.startswith("totals"):
        return "totals"
    if market_key.startswith("h2h"):
        return "h2h"
    return market_key


def _market_allowed_for_sport(sport: str, market_type: str) -> bool:
    sport_key = str(sport).strip().lower()
    market_family = _market_family(market_type)

    if market_family == "alternate":
        return ENABLE_ALTERNATE_MARKET_ALERTS
    if market_family == "partial":
        return ENABLE_PARTIAL_GAME_MARKET_ALERTS

    if sport_key == "basketball_nba":
        return market_family == "spreads" or (market_family == "totals" and ENABLE_NBA_TOTAL_ALERTS)
    if sport_key == "icehockey_nhl":
        return market_family == "spreads" or (market_family == "totals" and ENABLE_NHL_TOTAL_ALERTS)
    if sport_key == "baseball_mlb":
        return (
            (market_family == "h2h" and ENABLE_MLB_H2H_ALERTS)
            or (market_family == "spreads" and ENABLE_MLB_SPREAD_ALERTS)
            or (market_family == "totals" and ENABLE_MLB_TOTAL_ALERTS)
        )
    if sport_key == "americanfootball_nfl":
        return (
            (market_family == "spreads" and ENABLE_NFL_SPREAD_ALERTS)
            or (market_family == "totals" and ENABLE_NFL_TOTAL_ALERTS)
            or (market_family == "h2h" and ENABLE_NFL_H2H_ALERTS)
        )
    return market_family in {"spreads", "totals", "h2h"}


def _market_ev_threshold(market_type: str) -> float:
    market_family = _market_family(market_type)
    if market_family == "alternate":
        return UNIFIED_ALT_MARKET_EV_THRESHOLD
    if market_family == "partial":
        return UNIFIED_PARTIAL_MARKET_EV_THRESHOLD
    if market_family == "spreads":
        return UNIFIED_SPREAD_EV_THRESHOLD
    if market_family == "h2h":
        return UNIFIED_H2H_EV_THRESHOLD
    if market_family == "totals":
        return UNIFIED_TOTAL_EV_THRESHOLD
    return UNIFIED_EV_THRESHOLD


def _resolve_talent_prob(
    fair_probability: float,
    talent_ctx: TalentContext,
    home_team: str,
    away_team: str,
    outcome_name: str,
    market_type: str,
) -> float:
    """Blend sharp fair probability with the talent model for MLB matchups."""
    adj = talent_ctx.get(home_team, away_team)
    if adj is None:
        return fair_probability

    name_lower = outcome_name.strip().lower()
    home_lower = home_team.strip().lower()

    if market_type == "h2h":
        is_home = home_lower in name_lower or name_lower in home_lower
        model_prob = adj["model_prob_home"] if is_home else adj["model_prob_away"]
        return adjusted_fair_probability(fair_probability, model_prob)

    if market_type == "spreads":
        is_home = home_lower in name_lower or name_lower in home_lower
        model_prob = adj["model_prob_home"] if is_home else adj["model_prob_away"]
        spread_model = 0.50 + (model_prob - 0.50) * 0.50
        return adjusted_fair_probability(fair_probability, spread_model)

    if market_type == "totals":
        avg_fatigue = (adj["home_fatigue"] + adj["away_fatigue"]) / 2
        if name_lower == "over":
            return fair_probability + (avg_fatigue * 0.02)
        return fair_probability - (avg_fatigue * 0.02)

    return fair_probability


def _resolve_pdo_prob(
    fair_probability: float,
    pdo_ctx: PDOContext,
    home_team: str,
    away_team: str,
    outcome_name: str,
) -> float:
    """Adjust NHL totals probability using PDO regression signals."""
    home_data = pdo_ctx.get(home_team)
    away_data = pdo_ctx.get(away_team)
    if home_data is None or away_data is None:
        return fair_probability

    is_over = outcome_name.strip().lower() == "over"
    return pdo_total_adjustment(fair_probability, home_data, away_data, is_over)


def _resolve_pace_prob(
    fair_probability: float,
    pace_ctx: PaceContext,
    home_team: str,
    away_team: str,
    outcome_name: str,
) -> float:
    """Adjust NBA totals probability using lineup pace signals."""
    home_data = pace_ctx.get(home_team)
    away_data = pace_ctx.get(away_team)
    if home_data is None or away_data is None:
        return fair_probability

    is_over = outcome_name.strip().lower() == "over"
    return pace_total_adjustment(fair_probability, home_data, away_data, is_over)


def _resolve_proe_prob(
    fair_probability: float,
    proe_ctx: PROEContext,
    home_team: str,
    away_team: str,
    outcome_name: str,
    market_type: str,
) -> float:
    """Adjust NFL probability using PROE and per-play success signals."""
    home_data = proe_ctx.get(home_team)
    away_data = proe_ctx.get(away_team)
    if home_data is None or away_data is None:
        return fair_probability

    if market_type == "totals":
        is_over = outcome_name.strip().lower() == "over"
        return proe_total_adjustment(fair_probability, home_data, away_data, is_over)

    if market_type in ("spreads", "h2h"):
        name_lower = outcome_name.strip().lower()
        home_lower = home_team.strip().lower()
        is_home = home_lower in name_lower or name_lower in home_lower
        return proe_spread_adjustment(fair_probability, home_data, away_data, is_home)

    return fair_probability


def _prop_side(outcome_name: str) -> str:
    name = str(outcome_name or "").strip().lower()
    if "over" in name:
        return "over"
    if "under" in name:
        return "under"
    return name


def evaluate_player_props(
    event: dict,
    sport: str,
    soft_books: list,
    book_weights: dict,
) -> list:
    """Scan player-prop markets for a single event.

    Player props are quoted as binary Over/Under pairs per (player, line) with
    highly asymmetric juice, so the Pinnacle baseline is de-vigged with the
    multiplicative method:

        P_over = 1 / O_over ; P_under = 1 / O_under
        Sum = P_over + P_under
        P_true_over = P_over / Sum   (and symmetrically for the under)

    Returns a list of alert dicts ready for routing through services/alerts.py.
    """
    matchup = f"{event['away_team']} @ {event['home_team']}"
    # groups[(market_key, player, point)] = {"sharp": {side: price}, "soft": [offers]}
    groups: dict = {}

    for bookmaker in event.get("bookmakers", []):
        book_key = bookmaker.get("key")
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key", ""))
            if not _is_player_prop_market(market_key):
                continue
            for outcome in market.get("outcomes", []):
                player = str(outcome.get("description") or "").strip()
                side = _prop_side(outcome.get("name"))
                if not player or side not in {"over", "under"}:
                    continue
                try:
                    price = float(outcome["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if price <= 1.0:
                    continue
                point = str(outcome.get("point", ""))
                group = groups.setdefault(
                    (market_key, player, point),
                    {"sharp": {}, "soft": []},
                )
                if book_key == "pinnacle":
                    group["sharp"][side] = price
                elif book_key in soft_books:
                    group["soft"].append(
                        {
                            "book": bookmaker.get("title", book_key),
                            "book_key": book_key,
                            "side": side,
                            "price": price,
                        }
                    )

    alerts = []
    for (market_key, player, point), data in groups.items():
        sharp = data["sharp"]
        if "over" not in sharp or "under" not in sharp:
            continue

        implied = [decimal_implied_probability(sharp["over"]), decimal_implied_probability(sharp["under"])]
        fair = multiplicative_unvig(implied)
        if len(fair) != 2 or not all(fair):
            continue
        fair_by_side = {"over": fair[0], "under": fair[1]}

        best_by_side: dict = {}
        for offer in data["soft"]:
            side = offer["side"]
            if side not in fair_by_side:
                continue
            edge = calculate_edge_from_probability(offer["price"], fair_by_side[side])
            if edge < UNIFIED_PROP_EV_THRESHOLD:
                continue
            weighted = edge * book_weights.get(offer["book"], 1.0)
            current = best_by_side.get(side)
            if current is None or weighted > current["weighted"]:
                best_by_side[side] = {**offer, "edge": edge, "weighted": weighted}

        for side, offer in best_by_side.items():
            fair_probability = fair_by_side[side]
            fair_decimal = 1.0 / fair_probability
            point_text = f" {point}" if point else ""
            selection = f"{player} {side.upper()}{point_text}".strip()
            market_label = market_key.upper()
            if is_already_logged(matchup, market_label, selection):
                continue

            units = prop_kelly_units(
                offer["edge"], offer["price"], fraction=PROP_KELLY_FRACTION, cap=PROP_MAX_UNITS,
            )
            if units <= 0:
                continue

            fair_price_american = decimal_to_american(fair_decimal)
            pinnacle_price = sharp[side]
            was_logged = log_bet_to_db(
                matchup,
                market_label,
                selection,
                decimal_to_american(offer["price"]),
                offer["edge"],
                f"{units:.2f}",
                fair_price_american,
                sport,
                event["id"],
                notes=(
                    f"book={offer['book']};book_key={offer['book_key']};market=player_prop;"
                    f"stat={market_key};line={point};devig={PROP_DEVIG_METHOD};"
                    f"fair_probability={fair_probability:.4f};fair_decimal={fair_decimal:.4f}"
                ),
            )
            if not was_logged:
                print(f"Skipping prop alert because DB log failed for {selection}.")
                continue

            app_link = sportsbook_search_link(offer["book_key"], matchup)
            alerts.append(
                {
                    "sport": sport,
                    "description": (
                        f"**+EV PLAYER PROP ALERT**\n\n"
                        f"**Match:** {matchup}\n"
                        f"**Prop:** {selection} ({market_label})\n"
                        f"**Book:** [{offer['book']}]({app_link}) @ {decimal_to_american(offer['price'])}\n"
                        f"**Pinnacle:** {decimal_to_american(pinnacle_price)}\n"
                        f"**Fair Value:** {fair_price_american}\n"
                        f"**Edge:** {offer['edge'] * 100:.2f}%\n"
                        f"**De-vig:** {PROP_DEVIG_METHOD}\n"
                        f"**Suggested:** {units:.2f} Units"
                    ),
                }
            )
    return alerts


def scan_markets(cache_override=None, source: str = "unified_bot", alert_type: str = "bet_alert"):
    cache = cache_override if cache_override is not None else get_master_cache()
    if not cache:
        print("Cloud cache is empty. Run fetcher first.")
        return {"detail": "cache empty", "count": 0, "label": "alerts"}

    alerts = []
    near_misses = []
    scanned_candidates = []
    book_weights = get_book_weights()
    soft_books = ["fanduel", "draftkings", "betmgm", "bet365", "caesars", "bovada"]
    graded_bets = get_all_graded_bets()
    today_bets = get_today_bets()
    exposure_tracker = ExposureTracker() if ENABLE_CORRELATION_LIMITS else None

    talent_ctx = TalentContext()
    if "baseball_mlb" in cache:
        talent_ctx = build_talent_context()

    pdo_ctx = PDOContext()
    if "icehockey_nhl" in cache:
        pdo_ctx = build_pdo_context()

    pace_ctx = PaceContext()
    if "basketball_nba" in cache:
        pace_ctx = build_pace_context()

    proe_ctx = PROEContext()
    if "americanfootball_nfl" in cache:
        proe_ctx = build_proe_context()

    for sport, events in cache.items():
        for event in filter_valid_events(events, sport):
            matchup = f"{event['away_team']} @ {event['home_team']}"
            markets = {}

            for bookmaker in event.get("bookmakers", []):
                if not validate_bookmaker_outcomes(bookmaker):
                    continue
                for market in bookmaker.get("markets", []):
                    market_key = market["key"]
                    markets.setdefault(market_key, {"sharp": {}, "soft": []})

                    if bookmaker["key"] == "pinnacle":
                        for outcome in market.get("outcomes", []):
                            outcome_key = (
                                str(outcome["name"]).lower().strip(),
                                str(outcome.get("point", "")),
                            )
                            markets[market_key]["sharp"][outcome_key] = float(outcome["price"])
                    elif bookmaker["key"] in soft_books:
                        for outcome in market.get("outcomes", []):
                            markets[market_key]["soft"].append(
                                {
                                    "book": bookmaker["title"],
                                    "book_key": bookmaker["key"],
                                    "name": outcome["name"],
                                    "price": float(outcome["price"]),
                                    "point": outcome.get("point", ""),
                                    "id": outcome.get("id"),
                                }
                            )

            for market_type, data in markets.items():
                if not _market_allowed_for_sport(sport, market_type):
                    continue

                market_threshold = _market_ev_threshold(market_type)

                sharp = data["sharp"]
                if not sharp:
                    continue

                devig_method = "shin" if ENABLE_SHIN_DEVIG else DEVIG_METHOD
                fair_probabilities = fair_probabilities_from_prices(sharp, method=devig_method)

                time_decay = compute_time_decay(event.get("commence_time")) if ENABLE_TIME_DECAY else None
                sharp_prices_list = list(sharp.values())
                candidates = []
                for soft_bet in data["soft"]:
                    outcome_key = (
                        str(soft_bet["name"]).lower().strip(),
                        str(soft_bet.get("point", "")),
                    )
                    sharp_fair = fair_probabilities.get(outcome_key)
                    if not sharp_fair:
                        continue

                    if sport == "baseball_mlb" and talent_ctx.loaded:
                        fair_probability = _resolve_talent_prob(
                            sharp_fair, talent_ctx,
                            event["home_team"], event["away_team"],
                            soft_bet["name"], market_type,
                        )
                    elif sport == "icehockey_nhl" and pdo_ctx.loaded and market_type == "totals":
                        fair_probability = _resolve_pdo_prob(
                            sharp_fair, pdo_ctx,
                            event["home_team"], event["away_team"],
                            soft_bet["name"],
                        )
                    elif sport == "basketball_nba" and pace_ctx.loaded and market_type == "totals":
                        fair_probability = _resolve_pace_prob(
                            sharp_fair, pace_ctx,
                            event["home_team"], event["away_team"],
                            soft_bet["name"],
                        )
                    elif sport == "americanfootball_nfl" and proe_ctx.loaded:
                        fair_probability = _resolve_proe_prob(
                            sharp_fair, proe_ctx,
                            event["home_team"], event["away_team"],
                            soft_bet["name"], market_type,
                        )
                    else:
                        fair_probability = sharp_fair

                    fair_decimal = 1.0 / fair_probability
                    edge = calculate_edge_from_probability(soft_bet["price"], fair_probability)
                    book_weight = book_weights.get(soft_bet["book"], 1.0)
                    weighted_score = edge * book_weight

                    efficiency = None
                    if ENABLE_MARKET_EFFICIENCY:
                        soft_prices_for_market = [s["price"] for s in data["soft"]]
                        efficiency = score_market_efficiency(
                            sharp_prices_list, soft_prices_for_market, max(edge, 0.0),
                        )
                        weighted_score *= (0.5 + 0.5 * efficiency.score)

                    effective_threshold = market_threshold
                    if time_decay is not None:
                        effective_threshold = adjusted_threshold(market_threshold, time_decay)

                    scanned_candidates.append(
                        {
                            "matchup": matchup,
                            "selection": f"{soft_bet['name']} {soft_bet['point']}".strip(),
                            "book": soft_bet["book"],
                            "edge": edge,
                            "market": market_type,
                            "sport": sport,
                            "efficiency": efficiency.score if efficiency else None,
                            "time_phase": time_decay.phase if time_decay else None,
                        }
                    )

                    if UNIFIED_NEAR_MISS_THRESHOLD <= edge < effective_threshold:
                        near_misses.append(
                            {
                                "matchup": matchup,
                                "selection": f"{soft_bet['name']} {soft_bet['point']}".strip(),
                                "book": soft_bet["book"],
                                "edge": edge,
                                "weight": book_weight,
                                "market": market_type,
                            }
                        )

                    if edge >= effective_threshold:
                        if ENABLE_MARKET_EFFICIENCY and efficiency and efficiency.score < MIN_EFFICIENCY_SCORE:
                            continue

                        candidates.append(
                            {
                                "edge": edge,
                                "score": weighted_score,
                                "bet": soft_bet,
                                "pinnacle_price": sharp.get(outcome_key),
                                "fair_decimal": fair_decimal,
                                "fair_probability": fair_probability,
                                "book_weight": book_weight,
                                "outcome_key": outcome_key,
                                "efficiency": efficiency,
                                "time_decay": time_decay,
                            }
                        )

                if not candidates:
                    continue

                selected_candidates = []
                seen_outcomes = set()
                for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
                    if candidate["outcome_key"] in seen_outcomes:
                        continue
                    seen_outcomes.add(candidate["outcome_key"])
                    selected_candidates.append(candidate)
                    if len(selected_candidates) >= UNIFIED_MAX_ALERTS_PER_EVENT_MARKET:
                        break

                for candidate in selected_candidates:
                    final = candidate["bet"]
                    selection = f"{final['name']} {final['point']}".strip()
                    if is_already_logged(matchup, market_type, selection):
                        continue

                    edge = candidate["edge"]
                    offered_price = final["price"]
                    fair_decimal = candidate["fair_decimal"]
                    fair_probability = candidate["fair_probability"]
                    book_weight = candidate["book_weight"]
                    pinnacle_price = candidate.get("pinnacle_price")
                    pinnacle_text = (
                        f"**Pinnacle:** {decimal_to_american(pinnacle_price)}\n"
                        if pinnacle_price
                        else "**Pinnacle:** unavailable\n"
                    )
                    units = dynamic_kelly_units(edge, offered_price, graded_bets, today_bets)
                    fair_price_american = decimal_to_american(fair_decimal)

                    if exposure_tracker is not None:
                        teams = (event.get("home_team", ""), event.get("away_team", ""))
                        exp_decision = check_exposure(
                            exposure_tracker, str(event["id"]), market_type, units, teams,
                        )
                        if not exp_decision.allowed:
                            print(f"Exposure limit: {selection} -> {exp_decision.reason}")
                            continue
                        units = exp_decision.adjusted_units

                    was_logged = log_bet_to_db(
                        matchup,
                        market_type,
                        selection,
                        decimal_to_american(offered_price),
                        edge,
                        f"{units:.2f}",
                        fair_price_american,
                        sport,
                        event["id"],
                        notes=(
                            f"book={final['book']};book_key={final['book_key']};"
                            f"book_weight={book_weight:.4f};fair_probability={fair_probability:.4f};"
                            f"fair_decimal={fair_decimal:.4f}"
                        ),
                    )
                    if not was_logged:
                        print(f"Skipping alert because DB log failed for {selection}.")
                        continue

                    if exposure_tracker is not None:
                        teams = (event.get("home_team", ""), event.get("away_team", ""))
                        exposure_tracker.add(ExposureEntry(
                            event_id=str(event["id"]),
                            market_type=market_type,
                            side=selection,
                            matchup=matchup,
                            units=units,
                            edge=edge,
                            teams=teams,
                        ))

                    eff = candidate.get("efficiency")
                    td = candidate.get("time_decay")
                    eff_text = f"\n**Mkt Efficiency:** {eff.score:.0%}" if eff else ""
                    td_text = f"\n**Time Phase:** {td.phase} ({td.hours_to_event:.1f}h)" if td else ""

                    app_link = get_mobile_app_link(final["book_key"], final["id"], event["id"], matchup)
                    alerts.append(
                        {
                            "sport": sport,
                            "description": (
                                f"**+EV {market_type.upper()} ALERT**\n\n"
                                f"**Match:** {matchup}\n"
                                f"**Bet:** {selection}\n"
                                f"**Book:** [{final['book']}]({app_link}) @ {decimal_to_american(offered_price)}\n"
                                f"{pinnacle_text}"
                                f"**Fair Value:** {fair_price_american}\n"
                                f"**Edge:** {edge * 100:.2f}%\n"
                                f"**Book Weight:** {book_weight:.2f}x\n"
                                f"**Suggested:** {units:.2f} Units"
                                f"{eff_text}{td_text}"
                            )
                        }
                    )

            if ENABLE_PLAYER_PROP_ALERTS:
                alerts.extend(
                    evaluate_player_props(event, sport, soft_books, book_weights)
                )

    for index, alert in enumerate(alerts):
        send_discord_alert(
            {
                "embeds": [
                    {
                        "description": alert["description"],
                        "color": 3066993,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                ]
            },
            source=source,
            alert_type=alert_type,
            dedupe_key=alert["description"][:200],
            webhook_url=SPORT_ALERT_WEBHOOKS.get(alert.get("sport"), DISCORD_WEBHOOK_URL),
            add_bee_image=index == len(alerts) - 1,
        )

    near_miss_text = ""
    if near_misses:
        total_near_misses = len(near_misses)
        near_misses = sorted(near_misses, key=lambda item: item["edge"], reverse=True)[:3]
        samples = " | ".join(
            f"{item['matchup']} - {item['market'].upper()} - {item['selection']} @ {item['book']} ({item['edge'] * 100:.2f}%, {item['weight']:.2f}x)"
            for item in near_misses
        )
        near_miss_text = f"; near misses: {total_near_misses} total, top {len(near_misses)} -> {samples}"
    elif scanned_candidates:
        top_candidates = sorted(scanned_candidates, key=lambda item: item["edge"], reverse=True)[:3]
        samples = " | ".join(
            f"{item['sport']} {item['matchup']} - {item['market'].upper()} - {item['selection']} @ {item['book']} ({item['edge'] * 100:.2f}%)"
            for item in top_candidates
        )
        near_miss_text = f"; no threshold hits; top scanned edges -> {samples}"

    return {
        "detail": f"scan complete{near_miss_text}",
        "count": len(alerts),
        "label": "alerts",
        "meta": {"near_miss_summary": near_miss_text.lstrip('; ').strip()} if near_miss_text else {},
    }


if __name__ == "__main__":
    scan_markets()
