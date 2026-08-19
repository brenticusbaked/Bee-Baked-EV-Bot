import os
import time
from typing import Dict, List, Set

from db_manager import (
    get_master_cache,
    load_tracker_state,
    save_master_cache,
    save_tracker_state,
)
from services.http_client import request
from utils.config import env_flag
from utils.scratch_guard import filter_valid_events
from utils.seasons import filter_config_in_season
from utils.time import get_local_date_str, get_local_now


ODDS_API_KEY = os.getenv("ODDS_API_KEY")
ODDS_API_KEY_2 = os.getenv("ODDS_API_KEY_2")
ODDS_API_KEY_3 = os.getenv("ODDS_API_KEY_3")
# Held in reserve: a 500/month key cannot fund a per-event pull over a full
# slate, so no tier is currently billed to it.
ODDS_API_KEY_4 = os.getenv("ODDS_API_KEY_4")

# Per-run ceiling for the primary key (a 20,000/month plan).
ODDS_MAX_CREDITS_PER_RUN = int(os.getenv("ODDS_MAX_CREDITS_PER_RUN", "300"))
# The expansion keys are 500/month plans. At four runs a day that is ~4 credits
# per run each, so their ceiling has to be a different number entirely; a single
# shared ceiling either starves the primary key or drains the small ones.
ODDS_MAX_CREDITS_PER_RUN_SECONDARY = int(os.getenv("ODDS_MAX_CREDITS_PER_RUN_SECONDARY", "20"))
# Credits held back on each key, measured against its own x-requests-remaining.
# Default 0: spending the month out early is an accepted trade for more bets.
ODDS_MIN_CREDITS_RESERVE = int(os.getenv("ODDS_MIN_CREDITS_RESERVE", "0"))
# The event-scoped MLB pulls (first five, first-inning runs) cost per event per
# region, so they are affordable once a day rather than every window. Earliest
# local hour they may run; the first run at or after it claims the day.
ODDS_DAILY_PULL_MIN_LOCAL_HOUR = int(os.getenv("ODDS_DAILY_PULL_MIN_LOCAL_HOUR", "12"))
DAILY_PULL_STATE_KEY = "odds_daily_pull_state"
ODDS_MAX_EVENTS_PER_ENRICH = int(
    os.getenv("ODDS_MAX_EVENTS_PER_ENRICH", os.getenv("MAX_EVENTS_PER_ENRICH", "50"))
)
ODDS_API_REQUEST_DELAY_SECONDS = float(os.getenv("ODDS_API_REQUEST_DELAY_SECONDS", "0.3"))


class _CreditTracker:
    """Per-run spend cap for one API key, floored by the key's own quota.

    ``limit`` bounds this run, and the keys are on very different plans
    (20,000/month for the primary, 500/month for the expansion keys), so each
    gets its own. ``observe`` reads x-requests-remaining off any response, which
    additionally stops spend once the key would drop below ``reserve`` — 0 by
    default, i.e. a key is allowed to run itself dry.
    """

    def __init__(self, limit: int, reserve: int = ODDS_MIN_CREDITS_RESERVE) -> None:
        self.limit = limit
        self.used = 0
        self.reserve = reserve
        self.quota_remaining: int | None = None

    def observe(self, response) -> None:
        header = (getattr(response, "headers", None) or {}).get("x-requests-remaining")
        if header is None:
            return
        try:
            self.quota_remaining = int(float(header))
        except (TypeError, ValueError):
            return
        print(f"[odds] x-requests-remaining: {self.quota_remaining}")

    def charge(self, cost: int) -> bool:
        if cost <= 0:
            return True
        if cost > self.remaining:
            print(f"BEE-BAKED CREDIT GUARD: blocked {cost} credits; {self.remaining} left.")
            return False
        self.used += cost
        return True

    @property
    def remaining(self) -> int:
        run_remaining = max(0, self.limit - self.used)
        if self.quota_remaining is None:
            return run_remaining
        return min(run_remaining, max(0, self.quota_remaining - self.reserve))


# Define your most profitable prop markets
PLAYER_PROP_CONFIG = {
    # batter_total_bases and batter_hits were dropped: total bases is a superset
    # of hits, so the two priced nearly the same edge twice at 2 credits per
    # event per region each, and that spend buys the WNBA prop pull below.
    "baseball_mlb": "batter_home_runs,pitcher_strikeouts",
    "basketball_nba": "player_points,player_rebounds,player_assists,player_points_rebounds_assists,player_threes",
    "basketball_wnba": "player_points,player_rebounds,player_assists",
    "americanfootball_nfl": "player_pass_yds,player_rush_yds,player_receptions,player_receiving_yds"
}
ENABLE_PLAYER_PROPS_PULL = env_flag("ENABLE_PLAYER_PROPS_PULL", True)
# Sports whose player props are pulled once a day rather than every window.
# A WNBA slate is small and its props don't move enough between windows to pay
# 6 credits per event per region four times over.
DAILY_PROP_SPORTS = {"basketball_wnba"}

# Primary key stays on the low-cost core scan.
PRIMARY_CONFIG = {
    "basketball_nba": "spreads",
    "basketball_wnba": "spreads",
    "icehockey_nhl": "spreads",
    "baseball_mlb": "h2h",
}

# Secondary key is used only on full-game markets that add the most useful upside.
SECONDARY_CONFIG = {
    "basketball_nba": "h2h,totals",
    "basketball_wnba": "h2h,totals",
    "icehockey_nhl": "h2h",
}

# Tertiary key expands the scan into totals and MLB run-line style markets
TERTIARY_CONFIG = {
    "icehockey_nhl": "totals",
    "baseball_mlb": "spreads,totals",
}

SHARP_REGION = os.getenv("ODDS_API_SHARP_REGION", "eu")
SOFT_REGION = os.getenv("ODDS_API_SOFT_REGION", "us")
SHARP_BOOKS = os.getenv("ODDS_API_SHARP_BOOKS", "pinnacle")
DEFAULT_SOFT_BOOKS = "fanduel,draftkings,betmgm,bet365,caesars,bovada"
SOFT_BOOKS = os.getenv("ODDS_API_SOFT_BOOKS", DEFAULT_SOFT_BOOKS)
ENABLE_ODDS_SECONDARY_PULL = env_flag("ENABLE_ODDS_SECONDARY_PULL", True)
ENABLE_ODDS_TERTIARY_PULL = env_flag("ENABLE_ODDS_TERTIARY_PULL", True)
ENABLE_ODDS_PARTIAL_MARKET_PULL = env_flag("ENABLE_ODDS_PARTIAL_MARKET_PULL", False)
ENABLE_MLB_F5_PULL = env_flag("ENABLE_MLB_F5_PULL", True)
ENABLE_MLB_NRFI_PULL = env_flag("ENABLE_MLB_NRFI_PULL", True)

PARTIAL_GAME_CONFIG = {
    "basketball_nba": "alternate_spreads,alternate_totals,spreads_q1,totals_q1,h2h_q1,spreads_h1,totals_h1,h2h_h1",
    "icehockey_nhl": "alternate_spreads,alternate_totals,spreads_1st_period,totals_1st_period,h2h_1st_period",
    "baseball_mlb": "alternate_spreads,alternate_totals",
    "americanfootball_nfl": "alternate_spreads,alternate_totals,spreads_q1,totals_q1,h2h_q1,spreads_h1,totals_h1,h2h_h1",
}

MLB_F5_CONFIG = {
    "baseball_mlb": "h2h_1st_5_innings,spreads_1st_5_innings,totals_1st_5_innings",
}

MLB_NRFI_CONFIG = {
    "baseball_mlb": "runs_1st_inning",
}


def _claim_daily_pull(name: str) -> bool:
    """True at most once per local day, for the first run past the cutoff hour.

    Claimed through ``bot_state`` rather than inferred from the clock so a
    re-run, a shifted cron or a second workflow can't pay for the pull twice.
    """
    if get_local_now().hour < ODDS_DAILY_PULL_MIN_LOCAL_HOUR:
        return False

    today = get_local_date_str()
    state = load_tracker_state(DAILY_PULL_STATE_KEY, {}) or {}
    if state.get(name) == today:
        return False

    state[name] = today
    save_tracker_state(DAILY_PULL_STATE_KEY, state)
    return True


def _credits_for_config(config: Dict[str, str]) -> int:
    return sum(len(markets.split(",")) * 2 for markets in config.values())


def _merge_outcomes(existing_market: dict, incoming_market: dict) -> None:
    existing_outcomes = existing_market.setdefault("outcomes", [])
    outcome_index = {
        (str(outcome.get("name", "")).strip(), str(outcome.get("point", "")).strip()): outcome
        for outcome in existing_outcomes
    }
    for outcome in incoming_market.get("outcomes", []):
        key = (str(outcome.get("name", "")).strip(), str(outcome.get("point", "")).strip())
        outcome_index[key] = outcome
    existing_market["outcomes"] = list(outcome_index.values())


def _merge_bookmakers(existing_event: dict, incoming_event: dict) -> None:
    existing_books = existing_event.setdefault("bookmakers", [])
    book_index = {book.get("key"): book for book in existing_books}

    for incoming_book in incoming_event.get("bookmakers", []):
        incoming_key = incoming_book.get("key")
        if incoming_key not in book_index:
            existing_books.append(incoming_book)
            book_index[incoming_key] = incoming_book
            continue

        current_book = book_index[incoming_key]
        current_markets = current_book.setdefault("markets", [])
        market_index = {market.get("key"): market for market in current_markets}

        for incoming_market in incoming_book.get("markets", []):
            market_key = incoming_market.get("key")
            if market_key not in market_index:
                current_markets.append(incoming_market)
                market_index[market_key] = incoming_market
                continue
            _merge_outcomes(market_index[market_key], incoming_market)


def _merge_cache(cache: Dict[str, List[dict]], sport: str, events: List[dict]) -> None:
    if sport not in cache:
        cache[sport] = events
        return

    existing_events = cache[sport]
    event_index = {str(event.get("id")): event for event in existing_events}
    for event in events:
        event_id = str(event.get("id"))
        if event_id not in event_index:
            existing_events.append(event)
            event_index[event_id] = event
            continue
        _merge_bookmakers(event_index[event_id], event)


def _fetch_props_for_events(
    api_key: str,
    sport: str,
    events: List[dict],
    region: str,
    bookmakers: str,
    credit_tracker: _CreditTracker,
    max_enrich_events: int,
    daily_props_due: bool,
) -> None:
    """Iterates through active events and fetches player props individually."""
    if sport not in PLAYER_PROP_CONFIG or not ENABLE_PLAYER_PROPS_PULL:
        return
    if sport in DAILY_PROP_SPORTS and not daily_props_due:
        print(f"BEE-BAKED FETCH: {sport} props are a once-a-day pull; skipping this window.")
        return

    prop_markets = PLAYER_PROP_CONFIG[sport]
    request_credits = len(prop_markets.split(",")) * 2
    print(f"BEE-BAKED FETCH: Pulling [{prop_markets}] props for {len(events)} {sport} events ({region})...")

    enriched = 0
    for event in events:
        if enriched >= max_enrich_events:
            print(f"BEE-BAKED FETCH: prop enrichment cap reached ({max_enrich_events}) for {sport} ({region}).")
            break
        if credit_tracker.remaining < request_credits:
            print(f"BEE-BAKED FETCH: prop credit budget exhausted ({credit_tracker.remaining} left); stopping {sport} ({region}) enrichment.")
            break

        event_id = event.get("id")
        if not event_id:
            continue

        url = f"https://api.the-odds-api.com/v4/sports/{sport}/events/{event_id}/odds"
        params = {
            "apiKey": api_key,
            "regions": region,
            "markets": prop_markets,
            "bookmakers": bookmakers,
            "oddsFormat": "decimal",
        }

        try:
            response = request("GET", url, params=params, timeout=20)
            response.raise_for_status()
            prop_data = response.json()
            _merge_bookmakers(event, prop_data)
            credit_tracker.observe(response)
            if not credit_tracker.charge(request_credits):
                break
            enriched += 1
            time.sleep(0.3)
        except Exception as exc:
            print(f"Error fetching props for event {event_id} ({sport}): {exc}")


def _fetch_event_scoped_config(
    cache: Dict[str, List[dict]],
    api_key: str,
    config: Dict[str, str],
    label: str,
    region: str,
    bookmakers: str,
    credit_tracker: _CreditTracker,
    max_events: int,
) -> int:
    """Fetch markets that only exist on the per-event odds endpoint.

    The Odds API serves "additional markets" (first-five innings, first-inning
    runs) exclusively from ``/events/{id}/odds``; asking for them on the bulk
    ``/odds`` endpoint answers 422 and the pull is lost. Events already merged
    into the cache by the earlier pulls supply the ids, so no extra event-list
    request is needed.
    """
    success_count = 0
    for sport, markets in config.items():
        events = [event for event in cache.get(sport, []) or [] if event.get("id")]
        if not events:
            print(f"BEE-BAKED FETCH: no cached {sport} events to enrich via {label}; skipping.")
            continue

        request_credits = len(markets.split(",")) * 2
        enriched = 0
        for event in events[:max_events]:
            if credit_tracker.remaining < request_credits:
                print(f"BEE-BAKED FETCH: credit budget exhausted during {label}; stopping.")
                break

            url = f"https://api.the-odds-api.com/v4/sports/{sport}/events/{event['id']}/odds"
            params = {
                "apiKey": api_key,
                "regions": region,
                "markets": markets,
                "bookmakers": bookmakers,
                "oddsFormat": "decimal",
            }
            try:
                response = request("GET", url, params=params, timeout=20)
                _merge_bookmakers(event, response.json())
                credit_tracker.observe(response)
                if not credit_tracker.charge(request_credits):
                    break
                enriched += 1
                time.sleep(ODDS_API_REQUEST_DELAY_SECONDS)
            except Exception as exc:
                print(f"Error fetching {markets} for event {event['id']} ({sport}) via {label}: {exc}")

        if enriched:
            print(
                f"Cached {sport} ({markets}) via {label} for {enriched} event(s). "
                f"Credits total: {credit_tracker.used}/{credit_tracker.limit}"
            )
            success_count += 1
    return success_count


def _fetch_config(
    cache: Dict[str, List[dict]],
    api_key: str,
    config: Dict[str, str],
    label: str,
    region: str,
    bookmakers: str,
    fetched_props: Set[str],
    credit_tracker: _CreditTracker,
    max_enrich_events: int,
    daily_props_due: bool = False,
) -> int:
    success_count = 0
    for sport, markets in config.items():
        request_credits = len(markets.split(",")) * 2
        if credit_tracker.remaining < request_credits:
            print(f"BEE-BAKED FETCH: credit budget too low for {sport} ({markets}) via {label}; skipping.")
            continue

        url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
        params = {
            "apiKey": api_key,
            "regions": region,
            "markets": markets,
            "bookmakers": bookmakers,
            "oddsFormat": "decimal",
        }

        try:
            response = request("GET", url, params=params, timeout=20)
            events = filter_valid_events(response.json(), sport)
            credit_tracker.observe(response)
            if not credit_tracker.charge(request_credits):
                continue

            # Deduplicated prop fetch: Only pull player props ONCE per sport + region per run
            tracker_key = f"{sport}_{region}"
            if tracker_key not in fetched_props:
                _fetch_props_for_events(
                    api_key,
                    sport,
                    events,
                    region,
                    bookmakers,
                    credit_tracker,
                    max_enrich_events,
                    daily_props_due,
                )
                fetched_props.add(tracker_key)

            _merge_cache(cache, sport, events)
            print(f"Cached {sport} ({markets}) via {label}. Credits used this main request: {request_credits} | total: {credit_tracker.used}/{credit_tracker.limit}")
            success_count += 1
        except Exception as exc:
            print(f"Error fetching {sport} via {label}: {exc}")
    return success_count


def run_fetcher():
    if not ODDS_API_KEY:
        return {"detail": "ODDS_API_KEY missing", "count": 0, "label": "updates"}

    # One budget per API key. A single shared budget let the early tiers spend
    # the whole allowance and starved the later ones, even though each tier is
    # billed against a different key with its own monthly quota.
    trackers: Dict[str, _CreditTracker] = {}

    def credits_for(api_key: str | None, limit: int = ODDS_MAX_CREDITS_PER_RUN_SECONDARY) -> _CreditTracker:
        return trackers.setdefault(str(api_key or ""), _CreditTracker(limit))

    print(
        f"BEE-BAKED FETCH: credit caps per run — primary key {ODDS_MAX_CREDITS_PER_RUN},"
        f" expansion keys {ODDS_MAX_CREDITS_PER_RUN_SECONDARY}, reserve floor {ODDS_MIN_CREDITS_RESERVE}"
    )

    cache: Dict[str, List[dict]] = get_master_cache() or {}
    fetched_props: Set[str] = set()

    # Claimed before the first pull so the sharp and soft regions of the same
    # window both see it; claiming per region would price one side only.
    daily_props_due = _claim_daily_pull("daily_props") if ENABLE_PLAYER_PROPS_PULL else False
    if daily_props_due:
        print(
            "BEE-BAKED FETCH: daily prop window claimed; pulling"
            f" {', '.join(sorted(DAILY_PROP_SPORTS))} props"
        )

    primary_sharp = 0
    primary_soft = 0

    active_primary = filter_config_in_season(PRIMARY_CONFIG)
    skipped_primary = set(PRIMARY_CONFIG) - set(active_primary)
    if skipped_primary:
        print(f"BEE-BAKED FETCH: Skipping off-season sports (primary): {', '.join(sorted(skipped_primary))}")

    print(
        "BEE-BAKED FETCH: Running primary precision pull"
        f" ({_credits_for_config(active_primary) * 2} credits/run)"
    )
    primary_sharp = _fetch_config(cache, ODDS_API_KEY, active_primary, "primary sharp eu", SHARP_REGION, SHARP_BOOKS, fetched_props, credits_for(ODDS_API_KEY, ODDS_MAX_CREDITS_PER_RUN), ODDS_MAX_EVENTS_PER_ENRICH, daily_props_due)
    primary_soft = _fetch_config(cache, ODDS_API_KEY, active_primary, "primary soft us", SOFT_REGION, SOFT_BOOKS, fetched_props, credits_for(ODDS_API_KEY, ODDS_MAX_CREDITS_PER_RUN), ODDS_MAX_EVENTS_PER_ENRICH, daily_props_due)

    active_secondary = filter_config_in_season(SECONDARY_CONFIG)
    secondary_sharp = 0
    secondary_soft = 0
    if ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL:
        skipped_secondary = set(SECONDARY_CONFIG) - set(active_secondary)
        if skipped_secondary:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (secondary): {', '.join(sorted(skipped_secondary))}")
        print(
            "BEE-BAKED FETCH: Running secondary expansion pull"
            f" ({_credits_for_config(active_secondary) * 2} credits/run)"
        )
        secondary_sharp = _fetch_config(cache, ODDS_API_KEY_2, active_secondary, "secondary sharp eu", SHARP_REGION, SHARP_BOOKS, fetched_props, credits_for(ODDS_API_KEY_2), ODDS_MAX_EVENTS_PER_ENRICH, daily_props_due)
        secondary_soft = _fetch_config(cache, ODDS_API_KEY_2, active_secondary, "secondary soft us", SOFT_REGION, SOFT_BOOKS, fetched_props, credits_for(ODDS_API_KEY_2), ODDS_MAX_EVENTS_PER_ENRICH, daily_props_due)
    elif not ENABLE_ODDS_SECONDARY_PULL:
        print("ENABLE_ODDS_SECONDARY_PULL=false. Skipping secondary expansion pull.")
    else:
        print("ODDS_API_KEY_2 not set. Skipping secondary expansion pull.")

    active_tertiary = filter_config_in_season(TERTIARY_CONFIG)
    tertiary_sharp = 0
    tertiary_soft = 0
    if ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL:
        skipped_tertiary = set(TERTIARY_CONFIG) - set(active_tertiary)
        if skipped_tertiary:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (tertiary): {', '.join(sorted(skipped_tertiary))}")
        print(
            "BEE-BAKED FETCH: Running tertiary expansion pull"
            f" ({_credits_for_config(active_tertiary) * 2} credits/run)"
        )
        tertiary_sharp = _fetch_config(cache, ODDS_API_KEY_3, active_tertiary, "tertiary sharp eu", SHARP_REGION, SHARP_BOOKS, fetched_props, credits_for(ODDS_API_KEY_3), ODDS_MAX_EVENTS_PER_ENRICH, daily_props_due)
        tertiary_soft = _fetch_config(cache, ODDS_API_KEY_3, active_tertiary, "tertiary soft us", SOFT_REGION, SOFT_BOOKS, fetched_props, credits_for(ODDS_API_KEY_3), ODDS_MAX_EVENTS_PER_ENRICH, daily_props_due)
    elif not ENABLE_ODDS_TERTIARY_PULL:
        print("ENABLE_ODDS_TERTIARY_PULL=false. Skipping tertiary expansion pull.")
    else:
        print("ODDS_API_KEY_3 not set. Skipping tertiary expansion pull.")

    active_partial = filter_config_in_season(PARTIAL_GAME_CONFIG)
    partial_sharp = 0
    partial_soft = 0
    if ODDS_API_KEY_3 and ENABLE_ODDS_PARTIAL_MARKET_PULL:
        skipped_partial = set(PARTIAL_GAME_CONFIG) - set(active_partial)
        if skipped_partial:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (partial/alternate): {', '.join(sorted(skipped_partial))}")
        print(
            "BEE-BAKED FETCH: Running partial-game and alternate-market pull"
            f" ({_credits_for_config(active_partial) * 2} credits/run)"
        )
        partial_sharp = _fetch_config(cache, ODDS_API_KEY_3, active_partial, "partial/alternate sharp eu", SHARP_REGION, SHARP_BOOKS, fetched_props, credits_for(ODDS_API_KEY_3), ODDS_MAX_EVENTS_PER_ENRICH)
        partial_soft = _fetch_config(cache, ODDS_API_KEY_3, active_partial, "partial/alternate soft us", SOFT_REGION, SOFT_BOOKS, fetched_props, credits_for(ODDS_API_KEY_3), ODDS_MAX_EVENTS_PER_ENRICH)
    elif not ENABLE_ODDS_PARTIAL_MARKET_PULL:
        print("ENABLE_ODDS_PARTIAL_MARKET_PULL=false. Skipping partial-game and alternate-market pull.")
    else:
        print("ODDS_API_KEY_3 not set. Skipping partial-game and alternate-market pull.")

    # First five and NRFI are billed per event per region and share the primary
    # key's quota, so they run once a day instead of in every window.
    active_mlb_f5 = filter_config_in_season(MLB_F5_CONFIG)
    mlb_f5_sharp = 0
    mlb_f5_soft = 0
    daily_pull_due = _claim_daily_pull("mlb_f5") if ENABLE_MLB_F5_PULL else False
    if ODDS_API_KEY and ENABLE_MLB_F5_PULL and daily_pull_due:
        skipped_mlb_f5 = set(MLB_F5_CONFIG) - set(active_mlb_f5)
        if skipped_mlb_f5:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (mlb_f5): {', '.join(sorted(skipped_mlb_f5))}")
        print(
            "BEE-BAKED FETCH: Running dedicated MLB first-five pull"
            f" ({_credits_for_config(active_mlb_f5)} credits/event)"
        )
        mlb_f5_sharp = _fetch_event_scoped_config(cache, ODDS_API_KEY, active_mlb_f5, "mlb f5 sharp eu", SHARP_REGION, SHARP_BOOKS, credits_for(ODDS_API_KEY, ODDS_MAX_CREDITS_PER_RUN), ODDS_MAX_EVENTS_PER_ENRICH)
        mlb_f5_soft = _fetch_event_scoped_config(cache, ODDS_API_KEY, active_mlb_f5, "mlb f5 soft us", SOFT_REGION, SOFT_BOOKS, credits_for(ODDS_API_KEY, ODDS_MAX_CREDITS_PER_RUN), ODDS_MAX_EVENTS_PER_ENRICH)
    elif not ENABLE_MLB_F5_PULL:
        print("ENABLE_MLB_F5_PULL=false. Skipping dedicated MLB first-five pull.")
    elif not daily_pull_due:
        print("BEE-BAKED FETCH: MLB first-five already pulled today; skipping.")
    else:
        print("ODDS_API_KEY not set. Skipping dedicated MLB first-five pull.")

    active_mlb_nrfi = filter_config_in_season(MLB_NRFI_CONFIG)
    mlb_nrfi_sharp = 0
    mlb_nrfi_soft = 0
    nrfi_pull_due = _claim_daily_pull("mlb_nrfi") if ENABLE_MLB_NRFI_PULL else False
    if ODDS_API_KEY and ENABLE_MLB_NRFI_PULL and nrfi_pull_due:
        skipped_mlb_nrfi = set(MLB_NRFI_CONFIG) - set(active_mlb_nrfi)
        if skipped_mlb_nrfi:
            print(f"BEE-BAKED FETCH: Skipping off-season sports (mlb_nrfi): {', '.join(sorted(skipped_mlb_nrfi))}")
        print(
            "BEE-BAKED FETCH: Running dedicated MLB NRFI pull"
            f" ({_credits_for_config(active_mlb_nrfi)} credits/event)"
        )
        mlb_nrfi_sharp = _fetch_event_scoped_config(cache, ODDS_API_KEY, active_mlb_nrfi, "mlb nrfi sharp eu", SHARP_REGION, SHARP_BOOKS, credits_for(ODDS_API_KEY, ODDS_MAX_CREDITS_PER_RUN), ODDS_MAX_EVENTS_PER_ENRICH)
        mlb_nrfi_soft = _fetch_event_scoped_config(cache, ODDS_API_KEY, active_mlb_nrfi, "mlb nrfi soft us", SOFT_REGION, SOFT_BOOKS, credits_for(ODDS_API_KEY, ODDS_MAX_CREDITS_PER_RUN), ODDS_MAX_EVENTS_PER_ENRICH)
    elif not ENABLE_MLB_NRFI_PULL:
        print("ENABLE_MLB_NRFI_PULL=false. Skipping dedicated MLB NRFI pull.")
    elif not nrfi_pull_due:
        print("BEE-BAKED FETCH: MLB NRFI already pulled today; skipping.")
    else:
        print("ODDS_API_KEY not set. Skipping dedicated MLB NRFI pull.")

    save_master_cache(cache)

    primary_denom = len(active_primary)
    secondary_denom = len(active_secondary) if (ODDS_API_KEY_2 and ENABLE_ODDS_SECONDARY_PULL) else 0
    tertiary_denom = len(active_tertiary) if (ODDS_API_KEY_3 and ENABLE_ODDS_TERTIARY_PULL) else 0
    partial_denom = len(active_partial) if (ODDS_API_KEY_3 and ENABLE_ODDS_PARTIAL_MARKET_PULL) else 0
    mlb_f5_denom = len(active_mlb_f5) if (ODDS_API_KEY and ENABLE_MLB_F5_PULL and daily_pull_due) else 0
    mlb_nrfi_denom = len(active_mlb_nrfi) if (ODDS_API_KEY and ENABLE_MLB_NRFI_PULL and nrfi_pull_due) else 0

    detail = (
        f"fetch complete"
        f" | primary sharp: {primary_sharp}/{primary_denom} soft: {primary_soft}/{primary_denom}"
        f" | secondary sharp: {secondary_sharp}/{secondary_denom} soft: {secondary_soft}/{secondary_denom}"
        f" | tertiary sharp: {tertiary_sharp}/{tertiary_denom} soft: {tertiary_soft}/{tertiary_denom}"
        f" | partial/alternate sharp: {partial_sharp}/{partial_denom} soft: {partial_soft}/{partial_denom}"
        f" | mlb_f5 sharp: {mlb_f5_sharp}/{mlb_f5_denom} soft: {mlb_f5_soft}/{mlb_f5_denom}"
        f" | mlb_nrfi sharp: {mlb_nrfi_sharp}/{mlb_nrfi_denom} soft: {mlb_nrfi_soft}/{mlb_nrfi_denom}"
    )
    return {"detail": detail, "count": len(cache), "label": "updates"}


if __name__ == "__main__":
    run_fetcher()