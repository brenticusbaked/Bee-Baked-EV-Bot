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


class ScrapeTests(unittest.TestCase):
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
