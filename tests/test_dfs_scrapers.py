"""Offline tests for the DFS/exchange feeds: PrizePicks, Underdog, ProphetX.

Every payload here is a hand-built fixture in the shape each platform documents;
no test touches the network.
"""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scraper_prizepicks as prizepicks
import scraper_prophetx as prophetx
import scraper_underdog as underdog
from services import dfs_normalize
from services import scraper_api_client as client


def _cached_prop_event(event_id="evt-1", player="Aaron Judge", market="batter_home_runs", point=0.5):
    return {
        "id": event_id,
        "home_team": "New York Yankees",
        "away_team": "Boston Red Sox",
        "commence_time": "2026-08-19T23:05:00Z",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": market,
                        "outcomes": [
                            {"name": "Over", "description": player, "price": 3.4, "point": point},
                            {"name": "Under", "description": player, "price": 1.4, "point": point},
                        ],
                    }
                ],
            }
        ],
    }


def _underdog_payload(american_price="-122", status="active", stat="Home Runs"):
    return {
        "players": [
            {"id": "p1", "first_name": "Aaron", "last_name": "Judge", "sport_id": "MLB"},
        ],
        "appearances": [{"id": "a1", "player_id": "p1"}],
        "over_under_lines": [
            {
                "id": "l1",
                "status": status,
                "stat_value": "0.5",
                "over_under": {
                    "appearance_stat": {
                        "appearance_id": "a1",
                        "stat": "home_runs",
                        "display_stat": stat,
                    }
                },
                "options": [
                    {"choice": "higher", "american_price": american_price, "payout_multiplier": "0.94"},
                    {"choice": "lower", "american_price": "+100", "payout_multiplier": "1.03"},
                ],
            }
        ],
    }


def _prizepicks_payload(odds_type="standard", stat_type="Home Runs", line_score="1.5"):
    return {
        "data": [
            {
                "id": "proj-1",
                "attributes": {
                    "line_score": line_score,
                    "stat_type": stat_type,
                    "odds_type": odds_type,
                },
                "relationships": {"new_player": {"data": {"id": "pp1"}}},
            }
        ],
        "included": [
            {
                "id": "pp1",
                "type": "new_player",
                "attributes": {"display_name": "Aaron Judge", "league": "MLB", "team": "NYY"},
            }
        ],
    }


class StatMappingTests(unittest.TestCase):
    def test_platform_labels_collapse_onto_one_market_key(self):
        # PrizePicks says "3-PT Made", Underdog says "three_points_made".
        self.assertEqual(dfs_normalize.market_for_stat("3-PT Made"), "player_threes")
        self.assertEqual(dfs_normalize.market_for_stat("three_points_made"), "player_threes")
        self.assertEqual(dfs_normalize.market_for_stat("Pts+Rebs+Asts"), "player_points_rebounds_assists")

    def test_pitcher_and_hitter_strikeouts_are_different_markets(self):
        self.assertEqual(dfs_normalize.market_for_stat("Pitcher Strikeouts"), "pitcher_strikeouts")
        self.assertEqual(dfs_normalize.market_for_stat("Hitter Strikeouts"), "batter_strikeouts")

    def test_unmapped_stats_are_dropped_rather_than_guessed(self):
        self.assertIsNone(dfs_normalize.market_for_stat("Fantasy Score"))
        self.assertIsNone(dfs_normalize.sport_for_league("CBB"))

    def test_leagues_map_to_odds_api_sport_keys(self):
        self.assertEqual(dfs_normalize.sport_for_league("MLB"), "baseball_mlb")
        self.assertEqual(dfs_normalize.sport_for_league("wnba"), "basketball_wnba")


class PlayerMatchingTests(unittest.TestCase):
    """A DFS prop is only ingested when the cache already prices that player, so
    it lands on the same event id the sharp line is on."""

    def _line(self, player="Aaron Judge", point=0.5, price=1.9):
        return dfs_normalize.PropLine(
            player=player,
            sport_key="baseball_mlb",
            market_key="batter_home_runs",
            point=point,
            over_price=price,
            under_price=price,
        )

    def test_matched_player_attaches_to_the_cached_event(self):
        cache = {"baseball_mlb": [_cached_prop_event()]}
        events, counts = dfs_normalize.build_events("underdog", "Underdog", [self._line()], cache)
        self.assertEqual(counts, {"matched": 1, "unmatched": 0, "unpriced": 0})
        self.assertEqual(events[0]["id"], "evt-1")
        self.assertEqual(events[0]["bookmakers"][0]["key"], "underdog")
        outcomes = events[0]["bookmakers"][0]["markets"][0]["outcomes"]
        self.assertEqual([outcome["name"] for outcome in outcomes], ["Over", "Under"])
        self.assertEqual(outcomes[0]["description"], "Aaron Judge")

    def test_player_the_cache_does_not_price_is_dropped(self):
        cache = {"baseball_mlb": [_cached_prop_event()]}
        events, counts = dfs_normalize.build_events(
            "underdog", "Underdog", [self._line(player="Nobody Here")], cache
        )
        self.assertEqual(events, [])
        self.assertEqual(counts["unmatched"], 1)

    def test_unpriced_lines_never_reach_the_cache(self):
        cache = {"baseball_mlb": [_cached_prop_event()]}
        line = dfs_normalize.PropLine(
            player="Aaron Judge",
            sport_key="baseball_mlb",
            market_key="batter_home_runs",
            point=0.5,
        )
        events, counts = dfs_normalize.build_events("prizepicks", "PrizePicks", [line], cache)
        self.assertEqual(events, [])
        self.assertEqual(counts["unpriced"], 1)

    def test_two_markets_for_one_player_share_a_bookmaker_entry(self):
        cache = {"baseball_mlb": [_cached_prop_event()]}
        second = dfs_normalize.PropLine(
            player="Aaron Judge",
            sport_key="baseball_mlb",
            market_key="batter_hits",
            point=1.5,
            over_price=1.8,
        )
        events, _ = dfs_normalize.build_events(
            "underdog", "Underdog", [self._line(), second], cache
        )
        self.assertEqual(len(events), 1)
        markets = events[0]["bookmakers"][0]["markets"]
        self.assertEqual({market["key"] for market in markets}, {"batter_home_runs", "batter_hits"})

    def test_name_matching_ignores_punctuation_and_case(self):
        cache = {"baseball_mlb": [_cached_prop_event(player="Ronald Acuna Jr.")]}
        events, counts = dfs_normalize.build_events(
            "underdog", "Underdog", [self._line(player="ronald acuna jr")], cache
        )
        self.assertEqual(counts["matched"], 1)
        self.assertEqual(len(events), 1)


class UnderdogParsingTests(unittest.TestCase):
    def test_published_american_prices_become_decimal_odds(self):
        lines = underdog.parse_lines(_underdog_payload())
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual(line.player, "Aaron Judge")
        self.assertEqual(line.market_key, "batter_home_runs")
        self.assertEqual(line.point, 0.5)
        # -122 -> 1.8197, +100 -> 2.0
        self.assertAlmostEqual(line.over_price, 1.8197, places=3)
        self.assertAlmostEqual(line.under_price, 2.0, places=3)

    def test_payout_multiplier_alone_is_not_turned_into_a_price(self):
        payload = _underdog_payload()
        for option in payload["over_under_lines"][0]["options"]:
            option.pop("american_price")
        self.assertEqual(underdog.parse_lines(payload), [])

    def test_suspended_lines_are_skipped(self):
        self.assertEqual(underdog.parse_lines(_underdog_payload(status="suspended")), [])

    def test_line_without_a_joinable_player_is_skipped(self):
        payload = _underdog_payload()
        payload["appearances"] = []
        self.assertEqual(underdog.parse_lines(payload), [])

    def test_unmapped_stat_is_skipped(self):
        self.assertEqual(underdog.parse_lines(_underdog_payload(stat="Fantasy Points")), [])

    def test_scrape_ingests_matched_props_for_the_right_sport(self):
        with (
            patch.object(underdog, "fetch_json", return_value=_underdog_payload()) as fetch,
            patch.object(underdog, "get_master_cache", return_value={"baseball_mlb": [_cached_prop_event()]}),
            patch.object(underdog, "ingest_events") as ingest,
        ):
            result = underdog.scrape_underdog()

        self.assertEqual(fetch.call_args.args[0], underdog.OVER_UNDER_URL)
        self.assertEqual(fetch.call_args.args[1], "underdog")
        self.assertEqual(ingest.call_args.args[0], "baseball_mlb")
        self.assertEqual(result["count"], 1)

    def test_transport_failure_is_reported_not_raised(self):
        with patch.object(underdog, "fetch_json", side_effect=client.ScraperApiBlocked("403")):
            result = underdog.scrape_underdog()
        self.assertEqual(result["count"], 0)
        self.assertIn("unavailable", result["detail"])


class PrizePicksParsingTests(unittest.TestCase):
    def test_projections_are_unpriced_unless_a_leg_price_is_configured(self):
        lines = prizepicks.parse_lines(_prizepicks_payload(), None)
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0].is_priced)
        self.assertEqual(lines[0].point, 1.5)

    def test_configured_leg_price_applies_to_both_sides(self):
        lines = prizepicks.parse_lines(_prizepicks_payload(), 1.9)
        self.assertEqual((lines[0].over_price, lines[0].under_price), (1.9, 1.9))

    def test_demon_and_goblin_projections_are_excluded(self):
        self.assertEqual(prizepicks.parse_lines(_prizepicks_payload(odds_type="demon"), 1.9), [])
        self.assertEqual(prizepicks.parse_lines(_prizepicks_payload(odds_type="goblin"), 1.9), [])

    def test_leg_price_env_rejects_nonsense_values(self):
        for value in ("", "not-a-number", "0.5", "1.0"):
            with patch.dict("os.environ", {"PRIZEPICKS_LEG_DECIMAL_PRICE": value}, clear=False):
                self.assertIsNone(prizepicks.leg_decimal_price())
        with patch.dict("os.environ", {"PRIZEPICKS_LEG_DECIMAL_PRICE": "1.9"}, clear=False):
            self.assertEqual(prizepicks.leg_decimal_price(), 1.9)

    def test_league_ids_are_configurable(self):
        with patch.dict("os.environ", {"PRIZEPICKS_LEAGUE_IDS": "MLB:2,NBA:7"}, clear=False):
            self.assertEqual(prizepicks.league_ids(), {"MLB": "2", "NBA": "7"})
            targets = prizepicks._targets()
        self.assertEqual(len(targets), 2)
        self.assertIn("league_id=2", targets[0][0])
        self.assertIn("single_stat=true", targets[0][0])
        self.assertEqual(targets[0][1], "prizepicks")

    def test_line_differential_is_computed_against_the_market_line(self):
        cache = {"baseball_mlb": [_cached_prop_event(market="batter_hits", point=1.5)]}
        line = dfs_normalize.PropLine(
            player="Aaron Judge",
            sport_key="baseball_mlb",
            market_key="batter_hits",
            point=0.5,
        )
        differentials = prizepicks.line_differentials([line], cache)
        self.assertEqual(len(differentials), 1)
        self.assertEqual(differentials[0][1], 1.5)
        self.assertEqual(differentials[0][2], -1.0)

    def test_matching_lines_produce_no_differential(self):
        cache = {"baseball_mlb": [_cached_prop_event(market="batter_hits", point=1.5)]}
        line = dfs_normalize.PropLine(
            player="Aaron Judge", sport_key="baseball_mlb", market_key="batter_hits", point=1.5
        )
        self.assertEqual(prizepicks.line_differentials([line], cache), [])

    def test_unpriced_run_reports_without_writing_to_the_cache(self):
        with (
            patch.object(prizepicks, "gather_json_sync", return_value=[_prizepicks_payload()]),
            patch.object(prizepicks, "get_master_cache", return_value={"baseball_mlb": [_cached_prop_event()]}),
            patch.object(prizepicks, "ingest_events") as ingest,
            patch.dict("os.environ", {"PRIZEPICKS_LEG_DECIMAL_PRICE": ""}, clear=False),
        ):
            result = prizepicks.scrape_prizepicks()
        ingest.assert_not_called()
        self.assertIn("read-only", result["detail"])

    def test_priced_run_ingests(self):
        with (
            patch.object(prizepicks, "gather_json_sync", return_value=[_prizepicks_payload(line_score="0.5")]),
            patch.object(prizepicks, "get_master_cache", return_value={"baseball_mlb": [_cached_prop_event()]}),
            patch.object(prizepicks, "ingest_events") as ingest,
            patch.dict("os.environ", {"PRIZEPICKS_LEG_DECIMAL_PRICE": "1.9"}, clear=False),
        ):
            result = prizepicks.scrape_prizepicks()
        self.assertEqual(ingest.call_args.args[0], "baseball_mlb")
        self.assertEqual(result["count"], 1)


class ProphetXTests(unittest.TestCase):
    def _market(self):
        return {
            "market_id": 555,
            "event_id": "101",
            "market_type": "moneyline",
            "liquidity": 4250.0,
            "selections": [
                [{"outcome_id": 1, "name": "Patriots", "price": 1.95, "line": None, "quantity": 2100.0}],
                [{"outcome_id": 2, "name": "Jets", "price": 1.9, "line": None, "quantity": 2150.0}],
            ],
        }

    def _event(self):
        return {
            "event_id": "101",
            "name": "Patriots vs. Jets",
            "start_time": "2026-09-01T17:00:00Z",
            "home_team": "Patriots",
            "away_team": "Jets",
        }

    def test_both_sides_of_the_book_are_kept_with_their_liquidity(self):
        event = prophetx.build_event("americanfootball_nfl", self._event(), [self._market()])
        outcomes = event["bookmakers"][0]["markets"][0]["outcomes"]
        self.assertEqual(event["bookmakers"][0]["markets"][0]["key"], "h2h")
        self.assertEqual({outcome["name"] for outcome in outcomes}, {"Patriots", "Jets"})
        self.assertEqual({outcome["liquidity"] for outcome in outcomes}, {2100.0, 2150.0})

    def test_best_price_wins_and_equal_prices_pool_their_size(self):
        market = self._market()
        market["selections"] = [
            [
                {"name": "Patriots", "price": 1.95, "quantity": 100.0},
                {"name": "Patriots", "price": 2.05, "quantity": 50.0},
            ],
            [
                {"name": "Jets", "price": 1.9, "quantity": 10.0},
                {"name": "Jets", "price": 1.9, "quantity": 15.0},
            ],
        ]
        outcomes = {
            outcome["name"]: outcome
            for outcome in prophetx.build_event("americanfootball_nfl", self._event(), [market])[
                "bookmakers"
            ][0]["markets"][0]["outcomes"]
        }
        self.assertEqual(outcomes["Patriots"]["price"], 2.05)
        self.assertEqual(outcomes["Patriots"]["liquidity"], 50.0)
        self.assertEqual(outcomes["Jets"]["liquidity"], 25.0)

    def test_v2_flat_selections_parse_too(self):
        market = self._market()
        market["selections"] = [
            {"name": "Patriots", "price": 1.95, "quantity": 1.0},
            {"name": "Jets", "price": 1.9, "quantity": 1.0},
        ]
        event = prophetx.build_event("americanfootball_nfl", self._event(), [market])
        self.assertEqual(len(event["bookmakers"][0]["markets"][0]["outcomes"]), 2)

    def test_spread_and_total_lines_keep_their_point(self):
        market = self._market()
        market["market_type"] = "spread"
        market["selections"] = [[{"name": "Patriots", "price": 1.91, "line": -3.5, "quantity": 500.0}]]
        event = prophetx.build_event("americanfootball_nfl", self._event(), [market])
        outcome = event["bookmakers"][0]["markets"][0]["outcomes"][0]
        self.assertEqual(event["bookmakers"][0]["markets"][0]["key"], "spreads")
        self.assertEqual(outcome["point"], -3.5)

    def test_unknown_market_types_are_dropped(self):
        market = self._market()
        market["market_type"] = "player_props_experimental"
        self.assertIsNone(prophetx.build_event("americanfootball_nfl", self._event(), [market]))

    def test_multiple_markets_response_groups_by_event(self):
        keyed = prophetx._markets_by_event({"data": {"101": [self._market()]}})
        flat = prophetx._markets_by_event({"data": [self._market()]})
        self.assertEqual(list(keyed), ["101"])
        self.assertEqual(list(flat), ["101"])

    def test_missing_credentials_skip_the_task_instead_of_failing_the_run(self):
        with (
            patch.dict(
                "os.environ",
                {"PROPHETX_API_KEY": "", "PROPHETX_ACCESS_KEY": "", "PROPHETX_SECRET_KEY": ""},
                clear=False,
            ),
            patch.object(prophetx, "request") as request,
        ):
            result = prophetx.scrape_prophetx()
        request.assert_not_called()
        self.assertEqual(result["count"], 0)
        self.assertIn("skipped", result["detail"])

    def test_affiliate_key_is_sent_raw_without_a_bearer_prefix(self):
        with patch.dict("os.environ", {"PROPHETX_API_KEY": "affiliate-key"}, clear=False):
            response = MagicMock()
            response.json.return_value = {"data": {"tournaments": []}}
            with patch.object(prophetx, "request", return_value=response) as request:
                prophetx.scrape_prophetx()
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "affiliate-key")

    def test_access_secret_pair_is_exchanged_for_a_token(self):
        login = MagicMock()
        login.json.return_value = {"data": {"access_token": "token-123"}}
        tournaments = MagicMock()
        tournaments.json.return_value = {"data": {"tournaments": []}}
        with (
            patch.dict(
                "os.environ",
                {"PROPHETX_API_KEY": "", "PROPHETX_ACCESS_KEY": "ak", "PROPHETX_SECRET_KEY": "sk"},
                clear=False,
            ),
            patch.object(prophetx, "request", side_effect=[login, tournaments]) as request,
        ):
            prophetx.scrape_prophetx()

        self.assertEqual(request.call_args_list[0].args[0], "POST")
        self.assertEqual(request.call_args_list[0].kwargs["json"], {"access_key": "ak", "secret_key": "sk"})
        self.assertEqual(request.call_args_list[1].kwargs["headers"]["Authorization"], "token-123")

    def test_tournament_filter_maps_names_to_sport_keys(self):
        payload = {
            "data": {
                "tournaments": [
                    {"id": 1, "name": "NFL — Regular Season"},
                    {"id": 2, "name": "Table Tennis"},
                ]
            }
        }
        with patch.dict("os.environ", {"PROPHETX_TOURNAMENTS": "NFL,NBA"}, clear=False):
            selected = prophetx._wanted_tournaments(payload)
        self.assertEqual([item["id"] for item in selected], [1])
        self.assertEqual(selected[0]["_sport_key"], "americanfootball_nfl")

    def test_prophetx_never_routes_credentials_through_scraperapi(self):
        # An authenticated partner API has no WAF to bypass; proxying it would
        # expose the key to an extra hop.
        source = Path(prophetx.__file__).read_text()
        self.assertNotIn("scraper_api_client", source)


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        client.reset_run_state()

    def test_gather_json_bounds_in_flight_requests(self):
        in_flight = 0
        peak = 0

        def slow_fetch(url, book, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                return {"url": url}
            finally:
                in_flight -= 1

        targets = [(f"https://book.example/{index}", "prizepicks") for index in range(10)]
        with patch.object(client, "try_fetch_json", side_effect=slow_fetch):
            results = asyncio.run(client.gather_json(targets, concurrency=2))

        self.assertEqual(len(results), 10)
        self.assertLessEqual(peak, 2)

    def test_results_keep_input_order_and_failures_become_none(self):
        def fetch(url, book, **kwargs):
            return None if url.endswith("2") else {"url": url}

        targets = [(f"https://book.example/{index}", "underdog") for index in range(4)]
        with patch.object(client, "try_fetch_json", side_effect=fetch):
            results = asyncio.run(client.gather_json(targets, concurrency=4))

        self.assertEqual(results[2], None)
        self.assertEqual([result["url"] for result in results if result], [
            "https://book.example/0",
            "https://book.example/1",
            "https://book.example/3",
        ])

    def test_empty_target_list_makes_no_requests(self):
        with patch.object(client, "try_fetch_json") as fetch:
            self.assertEqual(asyncio.run(client.gather_json([])), [])
        fetch.assert_not_called()

    def test_dfs_books_use_premium_without_paying_for_rendering(self):
        for book in ("prizepicks", "underdog", "prophetx"):
            options = client.options_for(book)
            self.assertTrue(options.premium)
            self.assertFalse(options.render)
            self.assertEqual(options.credits, client.CREDITS_PREMIUM)


if __name__ == "__main__":
    unittest.main()
