"""Offline tests for the cache-backed model overlays.

Every case runs from ``mock_props_payload.json``. Nothing here touches a live
odds endpoint, Supabase, or Discord.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from typing import ClassVar
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import market_ev
from models.market_ev import MarketEdge, find_edges
from models.mlb_f5 import run_mlb_f5_model
from models.nfl_player_props import run_nfl_player_prop_model
from models.wnba_first_basket import run_wnba_first_basket_model
from services.bet_logic import outcome_matches, parse_selection

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_props_payload.json")


def load_mock_cache() -> dict:
    """Load the mock payload, stamping every event with a near-future start.

    The fixture stores a ``__FUTURE__`` placeholder so it never expires against
    ``utils.scratch_guard``, which drops events whose start time has passed.
    """
    with open(FIXTURE_PATH, "r", encoding="utf-8") as handle:
        cache = json.load(handle)

    commence_time = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    for events in cache.values():
        for event in events:
            if event.get("commence_time") == "__FUTURE__":
                event["commence_time"] = commence_time
    return cache


class MarketEdgeEngineTests(unittest.TestCase):
    def setUp(self):
        self.cache = load_mock_cache()

    def test_no_network_or_db_access_when_cache_is_injected(self):
        with patch.object(market_ev, "get_master_cache", side_effect=AssertionError("live cache read")):
            edges = find_edges(
                sport="baseball_mlb",
                market_keys=("totals_1st_5_innings",),
                ev_threshold=0.0,
                kelly_cap=1.0,
                cache=self.cache,
            )
        self.assertTrue(edges)

    def test_balanced_sharp_market_devigs_to_even_probability(self):
        edge = self._single_edge(
            sport="baseball_mlb",
            market_keys=("totals_1st_5_innings",),
            selection="Over 4.5",
        )
        # Pinnacle 1.95 / 1.95 is a symmetric market, so fair probability is 0.50.
        self.assertAlmostEqual(edge.fair_probability, 0.5, places=6)
        # BetMGM offers 2.09 -> EV = 2.09 * 0.50 - 1.
        self.assertAlmostEqual(edge.edge, 2.09 * 0.5 - 1.0, places=6)

    def test_quarter_kelly_units_match_the_shared_helper(self):
        edge = self._single_edge(
            sport="baseball_mlb",
            market_keys=("totals_1st_5_innings",),
            selection="Over 4.5",
        )
        expected = (edge.edge / (edge.offered_decimal - 1.0)) / 4.0 * 100.0
        self.assertAlmostEqual(edge.units, min(expected, 1.0), places=6)

    def test_sharp_book_is_never_reported_as_the_offering_book(self):
        edges = find_edges(
            sport="americanfootball_nfl",
            market_keys=("player_pass_yds", "player_anytime_td"),
            ev_threshold=-1.0,
            kelly_cap=1.0,
            group_by_player=True,
            cache=self.cache,
        )
        self.assertTrue(edges)
        self.assertNotIn("pinnacle", {edge.book_key for edge in edges})

    def test_missing_sharp_baseline_yields_no_edges(self):
        for event in self.cache["baseball_mlb"]:
            event["bookmakers"] = [book for book in event["bookmakers"] if book["key"] != "pinnacle"]
        edges = find_edges(
            sport="baseball_mlb",
            market_keys=("totals_1st_5_innings",),
            ev_threshold=-1.0,
            kelly_cap=1.0,
            cache=self.cache,
        )
        self.assertEqual(edges, [])

    def test_started_events_are_filtered_out(self):
        for event in self.cache["baseball_mlb"]:
            event["status"] = "completed"
        edges = find_edges(
            sport="baseball_mlb",
            market_keys=("totals_1st_5_innings",),
            ev_threshold=-1.0,
            kelly_cap=1.0,
            cache=self.cache,
        )
        self.assertEqual(edges, [])

    def _single_edge(self, sport: str, market_keys, selection: str) -> MarketEdge:
        edges = find_edges(
            sport=sport,
            market_keys=market_keys,
            ev_threshold=0.0,
            kelly_cap=1.0,
            cache=self.cache,
        )
        matches = [edge for edge in edges if edge.selection == selection]
        self.assertEqual(len(matches), 1, f"expected exactly one {selection} edge, got {edges}")
        return matches[0]


class NflPlayerPropTests(unittest.TestCase):
    def setUp(self):
        self.cache = load_mock_cache()

    def test_each_player_is_devigged_against_their_own_line(self):
        edges = find_edges(
            sport="americanfootball_nfl",
            market_keys=("player_pass_yds",),
            ev_threshold=0.0,
            kelly_cap=1.0,
            group_by_player=True,
            cache=self.cache,
        )
        allen = [edge for edge in edges if edge.player == "Josh Allen"]
        self.assertEqual(len(allen), 1)
        # Josh Allen's Pinnacle market is symmetric (1.90 / 1.90) even though
        # Joe Burrow's is not, which only holds if the two are grouped apart.
        self.assertAlmostEqual(allen[0].fair_probability, 0.5, places=6)
        self.assertAlmostEqual(allen[0].edge, 2.1 * 0.5 - 1.0, places=6)
        self.assertEqual(allen[0].selection, "Josh Allen Over 249.5")

    def test_anytime_td_yes_no_market_is_priced(self):
        edges = find_edges(
            sport="americanfootball_nfl",
            market_keys=("player_anytime_td",),
            ev_threshold=0.0,
            kelly_cap=1.0,
            group_by_player=True,
            cache=self.cache,
        )
        self.assertEqual([edge.selection for edge in edges], ["Ja'Marr Chase Yes"])
        self.assertGreater(edges[0].edge, 0.0)
        self.assertLess(edges[0].fair_probability, 1.0 / 2.1)

    def test_alternate_receiving_yards_key_is_recognized(self):
        edges = find_edges(
            sport="americanfootball_nfl",
            market_keys=("player_receiving_yds", "player_reception_yds"),
            ev_threshold=0.0,
            kelly_cap=1.0,
            group_by_player=True,
            cache=self.cache,
        )
        # Pinnacle posts player_receiving_yds, FanDuel posts player_reception_yds.
        self.assertEqual([edge.book_key for edge in edges], ["fanduel"])
        self.assertEqual(edges[0].selection, "Ja'Marr Chase Over 82.5")

    def test_run_model_is_offline_and_publishes_each_edge_once(self):
        published = self._run_with_stubs()
        self.assertTrue(published["count"] > 0)
        self.assertEqual(published["count"], len({alert["selection"] for alert in published["alerts"]}))

    def test_run_model_respects_the_alert_cap(self):
        with patch("models.nfl_player_props.NFL_PROP_MAX_ALERTS", 1):
            published = self._run_with_stubs()
        self.assertEqual(published["count"], 1)

    def test_run_model_skips_already_logged_selections(self):
        published = self._run_with_stubs(already_logged=True)
        self.assertEqual(published["count"], 0)

    def _run_with_stubs(self, already_logged: bool = False) -> dict:
        with (
            patch.object(market_ev, "is_already_logged", return_value=already_logged),
            patch.object(market_ev, "log_bet_to_db", return_value=True),
            patch.object(market_ev, "send_discord_alert", return_value=True),
            patch("models.nfl_player_props.is_sport_in_season", return_value=True),
        ):
            return run_nfl_player_prop_model(cache=self.cache)


class MlbFirstFiveTests(unittest.TestCase):
    def setUp(self):
        self.cache = load_mock_cache()

    def test_covers_f5_lines_and_totals(self):
        edges = find_edges(
            sport="baseball_mlb",
            market_keys=("h2h_1st_5_innings", "spreads_1st_5_innings", "totals_1st_5_innings"),
            ev_threshold=0.0,
            kelly_cap=1.0,
            cache=self.cache,
        )
        self.assertEqual(
            {edge.market_key for edge in edges},
            {"spreads_1st_5_innings", "totals_1st_5_innings"},
        )

    def test_run_model_no_ops_out_of_season(self):
        with patch("models.mlb_f5.is_sport_in_season", return_value=False):
            result = run_mlb_f5_model(cache=self.cache)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["detail"], "mlb off-season")

    def test_run_model_publishes_from_the_mock_payload(self):
        with (
            patch.object(market_ev, "is_already_logged", return_value=False),
            patch.object(market_ev, "log_bet_to_db", return_value=True),
            patch.object(market_ev, "send_discord_alert", return_value=True),
            patch("models.mlb_f5.is_sport_in_season", return_value=True),
        ):
            result = run_mlb_f5_model(cache=self.cache)
        self.assertGreater(result["count"], 0)

    def test_failed_db_write_suppresses_the_alert(self):
        with (
            patch.object(market_ev, "is_already_logged", return_value=False),
            patch.object(market_ev, "log_bet_to_db", return_value=False),
            patch.object(market_ev, "send_discord_alert", side_effect=AssertionError("alerted without a log")),
            patch("models.mlb_f5.is_sport_in_season", return_value=True),
        ):
            result = run_mlb_f5_model(cache=self.cache)
        self.assertEqual(result["count"], 0)


class WnbaFirstBasketTests(unittest.TestCase):
    def setUp(self):
        self.cache = load_mock_cache()

    def test_single_winner_market_is_devigged_as_one_n_way_group(self):
        edges = find_edges(
            sport="basketball_wnba",
            market_keys=("player_first_basket",),
            ev_threshold=-1.0,
            kelly_cap=0.5,
            group_by_player=False,
            cache=self.cache,
        )
        self.assertTrue(edges)
        # Pinnacle's book is overround, so every fair probability must land
        # below its raw implied probability.
        pinnacle_prices = {"A'ja Wilson": 5.5, "Breanna Stewart": 6.5, "Jackie Young": 7.0, "Sabrina Ionescu": 8.0}
        for edge in edges:
            raw_implied = 1.0 / pinnacle_prices[edge.selection]
            self.assertLess(edge.fair_probability, raw_implied)

    def test_only_soft_prices_beating_the_baseline_clear_the_threshold(self):
        edges = find_edges(
            sport="basketball_wnba",
            market_keys=("player_first_basket",),
            ev_threshold=0.0,
            kelly_cap=0.5,
            group_by_player=False,
            cache=self.cache,
        )
        # DraftKings shortens Stewart and Ionescu relative to Pinnacle; only the
        # players it prices longer can be +EV.
        self.assertTrue(edges)
        for edge in edges:
            self.assertGreater(edge.edge, 0.0)
            self.assertIn(edge.selection, {"A'ja Wilson", "Jackie Young"})

    def test_run_model_no_ops_out_of_season(self):
        with patch("models.wnba_first_basket.is_sport_in_season", return_value=False):
            result = run_wnba_first_basket_model(cache=self.cache)
        self.assertEqual(result["count"], 0)


class ClvResolvabilityTests(unittest.TestCase):
    """Every edge these models publish must be re-findable at closing time.

    ``clv_tracker`` re-prices a logged bet by feeding its stored market and
    selection through ``parse_selection`` and matching the result against
    Pinnacle's outcomes. A selection that cannot round-trip is silently skipped
    and never gets a CLV number, so the round-trip is asserted here.
    """

    RECEIVING_KEYS: ClassVar[set[str]] = {"player_receiving_yds", "player_reception_yds"}

    def setUp(self):
        self.cache = load_mock_cache()

    def test_every_published_market_round_trips_against_pinnacle(self):
        cases = [
            ("americanfootball_nfl", ("player_pass_yds", "player_anytime_td"), True),
            ("americanfootball_nfl", tuple(self.RECEIVING_KEYS), True),
            ("baseball_mlb", ("h2h_1st_5_innings", "spreads_1st_5_innings", "totals_1st_5_innings"), False),
            ("basketball_wnba", ("player_first_basket",), False),
        ]
        checked = 0
        for sport, market_keys, group_by_player in cases:
            edges = find_edges(
                sport=sport,
                market_keys=market_keys,
                ev_threshold=0.0,
                kelly_cap=1.0,
                group_by_player=group_by_player,
                cache=self.cache,
            )
            self.assertTrue(edges, f"no edges produced for {sport} {market_keys}")
            for edge in edges:
                self.assertIsNotNone(
                    self._pinnacle_close(edge),
                    f"clv_tracker could not re-price {edge.market_key} / {edge.selection}",
                )
                checked += 1
        self.assertGreater(checked, 0)

    def _pinnacle_close(self, edge: MarketEdge):
        spec = parse_selection(edge.market_key, edge.selection)
        self.assertNotEqual(spec.get("type"), "raw", f"unparsed selection: {edge.selection}")

        candidate_keys = {edge.market_key}
        if edge.market_key in self.RECEIVING_KEYS:
            candidate_keys |= self.RECEIVING_KEYS

        event = next(e for e in self.cache[edge.sport] if str(e["id"]) == edge.event_id)
        pinnacle = next(b for b in event["bookmakers"] if b["key"] == "pinnacle")
        for market in pinnacle["markets"]:
            if market["key"] not in candidate_keys:
                continue
            for outcome in market["outcomes"]:
                if outcome_matches(spec, outcome):
                    return float(outcome["price"])
        return None


if __name__ == "__main__":
    unittest.main()
