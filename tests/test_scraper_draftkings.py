import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scraper_draftkings
from services import scraper_api_client as client

FIXTURE = json.loads(
    (Path(__file__).with_name("mock_draftkings_markets.json")).read_text()
)
PROP_FIXTURE = json.loads(
    (Path(__file__).with_name("mock_draftkings_props.json")).read_text()
)

# Two prop entries as the league page embeds them: one under "Batter" and one
# under "Pitcher", which is what tells an ambiguous title like "Hits" apart.
NAV_HTML = (
    '{"id":"8RHTD82","parentId":"NavRoot","generatorId":"8RHTD82","seoId":"batter",'
    '"title":"Batter","sortOrder":1,"tags":[],"parameters":{"sportId":"7",'
    '"leagueId":"84240"},"children":['
    '{"id":"PZEOIML","parentId":"8RHTD82","generatorId":"g","seoId":"hits",'
    '"title":"Hits","sortOrder":1,"tags":["OSB","PlayerProps"],'
    '"parameters":{"sportId":"7","leagueId":"84240","categoryId":"743",'
    '"subcategoryId":"17320","marketTypeId":"13227"},"children":[]}]},'
    '{"id":"X2LC65P","parentId":"NavRoot","generatorId":"X2LC65P","seoId":"pitcher",'
    '"title":"Pitcher","sortOrder":2,"tags":[],"parameters":{"sportId":"7",'
    '"leagueId":"84240"},"children":['
    '{"id":"KMDYUB7","parentId":"X2LC65P","generatorId":"g","seoId":"hits",'
    '"title":"Hits","sortOrder":3,"tags":["OSB","PlayerProps"],'
    '"parameters":{"sportId":"7","leagueId":"84240","categoryId":"1924",'
    '"subcategoryId":"19457","marketTypeId":"16663"},"children":[]},'
    '{"id":"T8SY5ST","parentId":"X2LC65P","generatorId":"g","seoId":"game-lines",'
    '"title":"Game Lines","sortOrder":0,"tags":["OSB","PrimaryMarket"],'
    '"parameters":{"sportId":"7","leagueId":"84240","categoryId":"493",'
    '"subcategoryId":"4519"},"children":[]}]}'
)


class MarketParsingTests(unittest.TestCase):
    """The offer service returns events, markets and selections as three flat
    lists that have to be joined by id."""

    def setUp(self):
        self.by_market = scraper_draftkings._parse_market_lines(FIXTURE)

    def test_the_three_main_markets_are_parsed(self):
        self.assertEqual(sorted(self.by_market), ["h2h", "spreads", "totals"])
        for market_key in ("h2h", "spreads", "totals"):
            self.assertEqual(len(self.by_market[market_key]), 4, market_key)

    def test_prices_come_from_the_numeric_odds_not_the_display_string(self):
        # displayOdds.american is rendered with a Unicode minus, which float()
        # and int() both reject.
        for lines in self.by_market.values():
            for line in lines.values():
                self.assertIsInstance(line["price"], float)
                self.assertGreater(line["price"], 1.0)

    def test_spread_and_total_outcomes_keep_their_point(self):
        for market_key in ("spreads", "totals"):
            for line in self.by_market[market_key].values():
                self.assertIsNotNone(line["line"], market_key)

    def test_moneyline_outcomes_have_no_point(self):
        for line in self.by_market["h2h"].values():
            self.assertIsNone(line["line"])

    def test_home_and_away_come_from_the_participant_venue_role(self):
        line = next(iter(self.by_market["spreads"].values()))
        self.assertIn(" @ ", line["matchup"])
        away, home = line["matchup"].split(" @ ", 1)
        self.assertEqual(line["away_team"], away.strip())
        self.assertEqual(line["home_team"], home.strip())
        self.assertTrue(line["commence_time"])

    def test_alternate_and_period_markets_are_dropped(self):
        payload = json.loads(json.dumps(FIXTURE))
        payload["markets"][0]["name"] = "1st 5 Innings Run Line"
        payload["markets"][0]["marketType"] = {"name": "1st 5 Innings Run Line"}
        parsed = scraper_draftkings._parse_market_lines(payload)
        dropped = {
            key
            for key in self.by_market["h2h"]
            if key not in parsed.get("h2h", {})
        }
        self.assertTrue(dropped)

    def test_unpriced_selections_are_skipped(self):
        payload = json.loads(json.dumps(FIXTURE))
        for selection in payload["selections"]:
            selection.pop("trueOdds", None)
            selection["displayOdds"] = {"american": "\u2212163"}
        parsed = scraper_draftkings._parse_market_lines(payload)
        # The Unicode minus is normalised rather than dropping the whole slate.
        self.assertTrue(parsed)
        price = next(iter(parsed["h2h"].values()))["price"]
        self.assertAlmostEqual(price, 1.6135, places=3)

    def test_spread_parsing_helper_still_returns_only_spreads(self):
        self.assertEqual(
            scraper_draftkings._parse_spread_lines(FIXTURE),
            self.by_market["spreads"],
        )


class IngestShapeTests(unittest.TestCase):
    def test_every_market_lands_on_one_event_under_one_bookmaker(self):
        by_market = scraper_draftkings._parse_market_lines(FIXTURE)
        events = scraper_draftkings._to_ingest_events(by_market)

        self.assertEqual(len(events), 2)
        for event in events:
            self.assertEqual(len(event["bookmakers"]), 1)
            markets = event["bookmakers"][0]["markets"]
            self.assertEqual(
                sorted(market["key"] for market in markets),
                ["h2h", "spreads", "totals"],
            )
            for market in markets:
                self.assertEqual(len(market["outcomes"]), 2)
            self.assertTrue(event["home_team"])
            self.assertTrue(event["away_team"])


class LeagueDiscoveryTests(unittest.TestCase):
    """The page embeds every league DraftKings offers, so the id has to be
    matched by slug: taking the first ids on the page returned AFL for MLB."""

    PAGE = (
        '{"displayGroupId":41,"eventGroupId":79494,"eventGroupName":"AFL",'
        '"nameIdentifier":"afl"},'
        '{"displayGroupId":7,"eventGroupId":84240,"eventGroupName":"MLB",'
        '"nameIdentifier":"mlb"}'
    )

    def setUp(self):
        scraper_draftkings._PAGE_HTML_CACHE.clear()
        self.addCleanup(scraper_draftkings._PAGE_HTML_CACHE.clear)

    def test_the_league_id_is_matched_by_slug(self):
        with (
            patch.object(scraper_draftkings, "DK_LEAGUE_SLUG", "mlb"),
            patch.object(scraper_draftkings, "fetch", return_value=MagicMock(text=self.PAGE)) as fetch_mock,
        ):
            self.assertEqual(scraper_draftkings._discover_league_ids(), ["84240"])
        self.assertEqual(fetch_mock.call_args.args[0], scraper_draftkings.DK_PAGE_URL)

    def test_a_league_the_page_does_not_advertise_yields_nothing(self):
        with (
            patch.object(scraper_draftkings, "DK_LEAGUE_SLUG", "cfl"),
            patch.object(scraper_draftkings, "fetch", return_value=MagicMock(text=self.PAGE)),
        ):
            self.assertEqual(scraper_draftkings._discover_league_ids(), [])

    def test_discovery_failure_is_not_fatal(self):
        with patch.object(
            scraper_draftkings, "fetch", side_effect=client.ScraperApiError("blocked")
        ):
            self.assertEqual(scraper_draftkings._discover_league_ids(), [])

    def test_page_url_decides_the_league_and_the_slug(self):
        self.assertEqual(
            scraper_draftkings.DK_PAGE_URL,
            os.getenv(
                "DRAFTKINGS_PAGE_URL",
                "https://sportsbook.draftkings.com/leagues/baseball/mlb",
            ),
        )
        for url, expected in (
            ("https://sportsbook.draftkings.com/leagues/baseball/mlb", "baseball_mlb"),
            ("https://sportsbook.draftkings.com/leagues/basketball/nba", "basketball_nba"),
            ("https://sportsbook.draftkings.com/leagues/football/nfl", "americanfootball_nfl"),
            ("https://sportsbook.draftkings.com/", "baseball_mlb"),
        ):
            self.assertEqual(scraper_draftkings._sport_key_for_page(url), expected, url)
        self.assertEqual(
            scraper_draftkings._league_slug_for_page(
                "https://sportsbook.draftkings.com/leagues/hockey/nhl"
            ),
            "nhl",
        )


class TransportTests(unittest.TestCase):
    def test_the_league_query_is_folded_into_the_scraperapi_target(self):
        with (
            patch.object(scraper_draftkings, "DK_LEAGUE_IDS", ["84240"]),
            patch.object(scraper_draftkings, "fetch") as fetch_mock,
        ):
            fetch_mock.return_value = MagicMock(json=lambda: FIXTURE, text="")
            self.assertEqual(scraper_draftkings._fetch_dk_direct_payload(), FIXTURE)

        target = fetch_mock.call_args.args[0]
        self.assertIn("leagueId+eq+%2784240%27", target)
        self.assertIn("PrimaryMarket", target)
        self.assertTrue(fetch_mock.call_args.kwargs["options"].keep_headers)

    def test_a_league_returning_no_lines_falls_through_to_discovery(self):
        empty = {"events": [], "markets": [{"id": "1"}], "selections": []}
        with (
            patch.object(scraper_draftkings, "DK_LEAGUE_IDS", ["103"]),
            patch.object(
                scraper_draftkings, "fetch", return_value=MagicMock(json=lambda: empty, text="")
            ),
            patch.object(scraper_draftkings, "_discover_league_ids", return_value=[]) as discover,
        ):
            self.assertIsNone(scraper_draftkings._fetch_dk_direct_payload())
        discover.assert_called_once()

    def test_a_redirect_to_the_homepage_is_not_mistaken_for_a_payload(self):
        # The retired eventgroup route 301s to the marketing page, which arrives
        # as a 200 full of HTML.
        with (
            patch.object(scraper_draftkings, "DK_LEAGUE_IDS", ["84240"]),
            patch.object(scraper_draftkings, "_discover_league_ids", return_value=[]),
            patch.object(scraper_draftkings, "fetch") as fetch_mock,
        ):
            fetch_mock.return_value = MagicMock(
                json=MagicMock(side_effect=ValueError("no json")),
                text="<html><body>DraftKings Sportsbook</body></html>",
            )
            self.assertIsNone(scraper_draftkings._fetch_dk_direct_payload())

    def test_a_blocked_fetch_is_reported_not_raised(self):
        with (
            patch.object(scraper_draftkings, "DK_LEAGUE_IDS", ["84240"]),
            patch.object(scraper_draftkings, "_discover_league_ids", return_value=[]),
            patch.object(scraper_draftkings, "fetch", side_effect=client.ScraperApiError("403")),
        ):
            self.assertIsNone(scraper_draftkings._fetch_dk_direct_payload())


class PropDiscoveryTests(unittest.TestCase):
    def setUp(self):
        scraper_draftkings._PAGE_HTML_CACHE.clear()
        self.addCleanup(scraper_draftkings._PAGE_HTML_CACHE.clear)

    def test_prop_subcategories_are_keyed_by_their_navigation_group(self):
        with patch.object(scraper_draftkings, "_fetch_page_html", return_value=NAV_HTML):
            found = scraper_draftkings._discover_prop_subcategories("84240")

        # The same title under two groups is two different markets.
        self.assertEqual(
            found, [("batter_hits", "17320"), ("pitcher_hits_allowed", "19457")]
        )

    def test_primary_market_entries_are_not_mistaken_for_props(self):
        with patch.object(scraper_draftkings, "_fetch_page_html", return_value=NAV_HTML):
            found = scraper_draftkings._discover_prop_subcategories("84240")

        self.assertNotIn("4519", [subcategory for _, subcategory in found])

    def test_another_leagues_props_are_ignored(self):
        with patch.object(scraper_draftkings, "_fetch_page_html", return_value=NAV_HTML):
            self.assertEqual(scraper_draftkings._discover_prop_subcategories("88808"), [])

    def test_the_market_count_is_capped_so_a_run_cannot_drain_credits(self):
        with (
            patch.object(scraper_draftkings, "_fetch_page_html", return_value=NAV_HTML),
            patch.object(scraper_draftkings, "DK_PROP_MARKET_LIMIT", 1),
        ):
            self.assertEqual(len(scraper_draftkings._discover_prop_subcategories("84240")), 1)

    def test_a_failed_page_fetch_yields_no_props(self):
        with patch.object(scraper_draftkings, "_fetch_page_html", return_value=""):
            self.assertEqual(scraper_draftkings._discover_prop_subcategories("84240"), [])

    def test_the_page_is_only_fetched_once_per_process(self):
        response = MagicMock(text=NAV_HTML)
        with patch.object(scraper_draftkings, "fetch", return_value=response) as fetch:
            scraper_draftkings._fetch_page_html()
            scraper_draftkings._fetch_page_html()

        fetch.assert_called_once()


class PropParsingTests(unittest.TestCase):
    """DraftKings publishes props as a milestone ladder ("2+") or as an explicit
    over/under with a point; both have to land on the same market key."""

    def test_a_milestone_ladder_becomes_over_lines_half_a_unit_below(self):
        lines = scraper_draftkings._to_prop_lines(
            PROP_FIXTURE["milestone"], "batter_home_runs"
        )
        by_player = {(line.player, line.point): line for line in lines}

        # "1+ home runs" is the same wager as over 0.5.
        self.assertEqual(sorted({point for _, point in by_player}), [0.5, 1.5])
        line = by_player[("Brandon Lowe", 0.5)]
        self.assertEqual(line.market_key, "batter_home_runs")
        self.assertAlmostEqual(line.over_price, 4.73)
        self.assertIsNone(line.under_price)

    def test_every_player_in_a_market_is_kept(self):
        lines = scraper_draftkings._to_prop_lines(
            PROP_FIXTURE["milestone"], "batter_home_runs"
        )
        self.assertEqual({line.player for line in lines}, {"Brandon Lowe", "Oneil Cruz"})

    def test_an_over_under_market_pairs_both_sides_on_one_line(self):
        lines = scraper_draftkings._to_prop_lines(
            PROP_FIXTURE["over_under"], "pitcher_outs"
        )

        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertEqual((line.player, line.point), ("Paul Skenes", 15.5))
        self.assertAlmostEqual(line.over_price, 1.84, places=2)
        self.assertAlmostEqual(line.under_price, 1.89, places=2)

    def test_props_are_ingested_under_the_market_key_with_the_player_described(self):
        lines = scraper_draftkings._to_prop_lines(
            PROP_FIXTURE["over_under"], "pitcher_outs"
        )
        cache = {
            "baseball_mlb": [
                {
                    "id": "cached-event",
                    "home_team": "Pittsburgh Pirates",
                    "away_team": "Detroit Tigers",
                    "commence_time": "2026-08-19T16:35:00Z",
                    "bookmakers": [
                        {
                            "key": "pinnacle",
                            "markets": [
                                {
                                    "key": "pitcher_outs",
                                    "outcomes": [
                                        {
                                            "name": "Over",
                                            "description": "Paul Skenes",
                                            "price": 1.9,
                                            "point": 15.5,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        with patch.object(scraper_draftkings, "DK_SPORT_KEY", "baseball_mlb"):
            events, counts = scraper_draftkings.build_prop_events(
                "draftkings", "DraftKings", lines, cache
            )

        self.assertEqual(counts["matched"], 1)
        market = events[0]["bookmakers"][0]["markets"][0]
        self.assertEqual(market["key"], "pitcher_outs")
        self.assertEqual(
            {(outcome["name"], outcome["description"]) for outcome in market["outcomes"]},
            {("Over", "Paul Skenes"), ("Under", "Paul Skenes")},
        )

    def test_an_unpriced_or_unlabelled_selection_is_dropped(self):
        payload = {
            "events": PROP_FIXTURE["over_under"]["events"],
            "markets": PROP_FIXTURE["over_under"]["markets"],
            "selections": [
                {"marketId": "x", "label": "Over", "points": 1.5},
                {"marketId": "x", "trueOdds": 1.9},
            ],
        }
        self.assertEqual(scraper_draftkings._to_prop_lines(payload, "pitcher_outs"), [])

    def test_props_can_be_turned_off_without_touching_the_main_markets(self):
        with (
            patch.object(scraper_draftkings, "ENABLE_DK_PROPS", False),
            patch.object(scraper_draftkings, "_fetch_page_html") as page,
        ):
            self.assertEqual(scraper_draftkings._scrape_prop_events(FIXTURE), [])

        page.assert_not_called()

    def test_a_prop_fetch_failure_does_not_fail_the_scrape(self):
        with (
            patch.object(
                scraper_draftkings,
                "_fetch_prop_lines",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(scraper_draftkings, "_league_id_from_payload", return_value="84240"),
        ):
            self.assertEqual(scraper_draftkings._scrape_prop_events(FIXTURE), [])


class ScrapeTests(unittest.TestCase):
    def setUp(self):
        props = patch.object(scraper_draftkings, "_scrape_prop_events", return_value=[])
        props.start()
        self.addCleanup(props.stop)

    def test_a_successful_scrape_ingests_all_markets_under_the_page_league(self):
        with (
            patch.object(scraper_draftkings, "_fetch_dk_direct_payload", return_value=FIXTURE),
            patch.object(scraper_draftkings, "load_previous_lines", return_value={}),
            patch.object(scraper_draftkings, "save_current_lines"),
            patch.object(scraper_draftkings, "ingest_events") as ingest,
            patch.object(scraper_draftkings, "post_discord") as discord,
            patch.object(scraper_draftkings, "DK_SPORT_KEY", "baseball_mlb"),
        ):
            result = scraper_draftkings.scrape_dk()

        self.assertEqual(result["count"], 0)
        discord.assert_not_called()
        self.assertEqual(ingest.call_args.args[0], "baseball_mlb")
        self.assertEqual(len(ingest.call_args.args[1]), 2)

    def test_a_moved_spread_alerts_discord(self):
        spreads = scraper_draftkings._parse_market_lines(FIXTURE)["spreads"]
        key = next(iter(spreads))
        previous = {key: {"line": float(spreads[key]["line"]) + 2}}

        with (
            patch.object(scraper_draftkings, "_fetch_dk_direct_payload", return_value=FIXTURE),
            patch.object(scraper_draftkings, "load_previous_lines", return_value=previous),
            patch.object(scraper_draftkings, "save_current_lines"),
            patch.object(scraper_draftkings, "ingest_events"),
            patch.object(scraper_draftkings, "_pinnacle_reference", return_value="n/a"),
            patch.object(scraper_draftkings, "post_discord") as discord,
        ):
            result = scraper_draftkings.scrape_dk()

        self.assertEqual(result["count"], 1)
        discord.assert_called_once()

    def test_no_payload_reports_no_data_without_writing(self):
        with (
            patch.object(scraper_draftkings, "_fetch_dk_direct_payload", return_value=None),
            patch.object(scraper_draftkings, "save_current_lines") as save,
            patch.object(scraper_draftkings, "ingest_events") as ingest,
        ):
            result = scraper_draftkings.scrape_dk()

        self.assertEqual(result["detail"], "draftkings scrape no data")
        save.assert_not_called()
        ingest.assert_not_called()

    def test_a_missing_tracker_state_does_not_break_the_steam_check(self):
        with (
            patch.object(scraper_draftkings, "_fetch_dk_direct_payload", return_value=FIXTURE),
            patch.object(scraper_draftkings, "load_previous_lines", return_value=None),
            patch.object(scraper_draftkings, "save_current_lines"),
            patch.object(scraper_draftkings, "ingest_events"),
            patch.object(scraper_draftkings, "post_discord"),
        ):
            self.assertEqual(scraper_draftkings.scrape_dk()["count"], 0)


if __name__ == "__main__":
    unittest.main()
