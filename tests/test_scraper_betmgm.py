import unittest
from unittest.mock import MagicMock, patch

import scraper_betmgm


def _option(name: str, decimal: float, source: str | None = None) -> dict:
    option = {"name": {"value": name}, "price": {"decimal": decimal}}
    if source is not None:
        option["sourceName"] = {"value": source}
    return option


CDS_PAYLOAD = {
    "fixtures": [
        {
            "id": "2:12345",
            "name": {"value": "Chicago Cubs at St. Louis Cardinals"},
            "startDate": "2026-08-19T23:15:00Z",
            "competition": {"name": {"value": "MLB"}},
            "optionMarkets": [
                {
                    "name": {"value": "Run Line"},
                    "options": [
                        _option("Chicago Cubs", 1.87, "+1.5"),
                        _option("St. Louis Cardinals", 2.05, "-1.5"),
                    ],
                },
                {
                    "name": {"value": "Money Line"},
                    "options": [
                        _option("Chicago Cubs", 2.30),
                        _option("St. Louis Cardinals", 1.65),
                    ],
                },
                {
                    "name": {"value": "Total"},
                    "options": [
                        _option("Over", 1.91, "8.5"),
                        _option("Under", 1.95, "8.5"),
                    ],
                },
                {
                    "name": {"value": "1st 5 Innings Run Line"},
                    "options": [_option("Chicago Cubs", 1.80, "+0.5")],
                },
            ],
        }
    ]
}


class MarketClassificationTests(unittest.TestCase):
    def test_main_markets_map_to_master_cache_keys(self):
        self.assertEqual(scraper_betmgm._classify_market("Run Line"), "spreads")
        self.assertEqual(scraper_betmgm._classify_market("Point Spread"), "spreads")
        self.assertEqual(scraper_betmgm._classify_market("Money Line"), "h2h")
        self.assertEqual(scraper_betmgm._classify_market("Total"), "totals")

    def test_period_and_player_markets_are_skipped(self):
        for name in (
            "1st 5 Innings Run Line",
            "First Half Spread",
            "Player Total Bases",
            "Team Total Runs",
            "",
        ):
            with self.subTest(name=name):
                self.assertIsNone(scraper_betmgm._classify_market(name))


class MarketLineBuildTests(unittest.TestCase):
    def test_spreads_moneyline_and_totals_are_grouped_by_market(self):
        by_market = scraper_betmgm._build_market_lines(CDS_PAYLOAD)

        self.assertEqual(sorted(by_market), ["h2h", "spreads", "totals"])
        self.assertEqual(len(by_market["spreads"]), 2)
        self.assertEqual(len(by_market["h2h"]), 2)
        self.assertEqual(len(by_market["totals"]), 2)

    def test_spread_line_price_and_matchup_are_carried_through(self):
        spreads = scraper_betmgm._build_market_lines(CDS_PAYLOAD)["spreads"]
        cubs = spreads["2:12345_Chicago Cubs"]

        self.assertEqual(cubs["matchup"], "Chicago Cubs at St. Louis Cardinals")
        self.assertEqual(cubs["commence_time"], "2026-08-19T23:15:00Z")
        self.assertEqual(cubs["line"], "+1.5")
        self.assertEqual(cubs["price"], 1.87)

    def test_moneyline_outcomes_carry_no_point(self):
        h2h = scraper_betmgm._build_market_lines(CDS_PAYLOAD)["h2h"]
        self.assertIsNone(h2h["2:12345_Chicago Cubs"]["line"])

    def test_period_market_does_not_overwrite_the_full_game_spread(self):
        spreads = scraper_betmgm._build_market_lines(CDS_PAYLOAD)["spreads"]
        self.assertEqual(spreads["2:12345_Chicago Cubs"]["line"], "+1.5")

    def test_build_current_lines_still_returns_spreads_only(self):
        self.assertEqual(
            scraper_betmgm._build_current_lines(CDS_PAYLOAD),
            scraper_betmgm._build_market_lines(CDS_PAYLOAD)["spreads"],
        )


class CompetitionFilterTests(unittest.TestCase):
    def test_fixtures_outside_the_configured_competition_are_dropped(self):
        fixture = {"competition": {"name": {"value": "Nippon Professional Baseball"}}}
        with patch.object(scraper_betmgm, "BETMGM_COMPETITION_NAME", "MLB"):
            self.assertFalse(scraper_betmgm._matches_configured_competition(fixture))
            self.assertTrue(
                scraper_betmgm._matches_configured_competition(
                    {"competition": {"name": {"value": "MLB"}}}
                )
            )

    def test_empty_competition_filter_keeps_every_fixture(self):
        with patch.object(scraper_betmgm, "BETMGM_COMPETITION_NAME", ""):
            self.assertTrue(scraper_betmgm._matches_configured_competition({}))


class AccessIdTests(unittest.TestCase):
    def test_access_id_is_read_from_a_query_string_or_config_body(self):
        self.assertEqual(
            scraper_betmgm._access_id_from_text("fetch('/cds-api/x?x-bwin-accessid=MTIzNDU2Nzg5MDEyMzQ1Ng&lang=en')"),
            "MTIzNDU2Nzg5MDEyMzQ1Ng",
        )
        self.assertEqual(
            scraper_betmgm._access_id_from_text('{"accessId":"OTg3NjU0MzIxMDk4NzY1NA","lang":"en"}'),
            "OTg3NjU0MzIxMDk4NzY1NA",
        )
        self.assertEqual(scraper_betmgm._access_id_from_text("nothing here"), "")

    def test_discovery_caches_the_token_it_finds(self):
        response = MagicMock(text='{"accessId":"TOKENTOKENTOKEN1"}')
        with (
            patch.object(scraper_betmgm, "_sa_get", return_value=response) as sa_get,
            patch.object(scraper_betmgm, "_store_access_id") as store,
        ):
            self.assertEqual(scraper_betmgm._discover_access_id(), "TOKENTOKENTOKEN1")
        store.assert_called_once_with("TOKENTOKENTOKEN1")
        self.assertFalse(sa_get.call_args.kwargs["render"])

    def test_a_rejected_token_is_cleared_and_rediscovered_once(self):
        rejected = MagicMock(status_code=400, text='{"message":"Access id is invalid"}')
        accepted = MagicMock(status_code=200, text='{"fixtures":[]}')
        with (
            patch.object(scraper_betmgm, "BETMGM_ACCESS_ID", "STALE"),
            patch.object(scraper_betmgm, "_cached_access_id", return_value=""),
            patch.object(scraper_betmgm, "_store_access_id") as store,
            patch.object(scraper_betmgm, "_discover_access_id", return_value="FRESH") as discover,
            patch.object(scraper_betmgm, "_cds_get", side_effect=[rejected, accepted]) as cds_get,
        ):
            self.assertEqual(scraper_betmgm._fetch_betmgm_cds_markets(), {})

        store.assert_called_once_with("")
        discover.assert_called_once()
        self.assertEqual([call.args[0] for call in cds_get.call_args_list], ["STALE", "FRESH"])


class CdsTransportTests(unittest.TestCase):
    def test_the_fixtures_call_is_made_directly_before_paying_for_a_proxy(self):
        direct = MagicMock(status_code=200, text='{"fixtures":[]}')
        with (
            patch.object(scraper_betmgm, "http_request", return_value=direct) as http,
            patch.object(scraper_betmgm, "_sa_get") as sa_get,
        ):
            self.assertIs(scraper_betmgm._cds_get("TOKEN"), direct)

        sa_get.assert_not_called()
        self.assertEqual(http.call_args.kwargs["params"]["x-bwin-accessid"], "TOKEN")
        self.assertEqual(http.call_args.kwargs["headers"]["x-bwin-accessid"], "TOKEN")

    def test_a_blocked_direct_call_falls_back_to_scraperapi_without_rendering(self):
        blocked = MagicMock(status_code=403, text="<html>challenge</html>")
        proxied = MagicMock(status_code=200, text='{"fixtures":[]}')
        with (
            patch.object(scraper_betmgm, "http_request", return_value=blocked),
            patch.object(scraper_betmgm, "_sa_get", return_value=proxied) as sa_get,
        ):
            self.assertIs(scraper_betmgm._cds_get("TOKEN"), proxied)

        self.assertNotIn("render", sa_get.call_args.kwargs)

    def test_a_direct_network_failure_falls_back_to_scraperapi(self):
        proxied = MagicMock(status_code=200, text='{"fixtures":[]}')
        with (
            patch.object(scraper_betmgm, "http_request", side_effect=OSError("connection reset")),
            patch.object(scraper_betmgm, "_sa_get", return_value=proxied) as sa_get,
        ):
            self.assertIs(scraper_betmgm._cds_get("TOKEN"), proxied)
        sa_get.assert_called_once()


class IngestTests(unittest.TestCase):
    def test_every_parsed_market_is_merged_into_the_master_cache(self):
        by_market = scraper_betmgm._build_market_lines(CDS_PAYLOAD)
        with (
            patch.object(scraper_betmgm, "_fetch_betmgm_cds_markets", return_value=by_market),
            patch.object(scraper_betmgm, "load_previous_lines", return_value={}),
            patch.object(scraper_betmgm, "save_current_lines"),
            patch.object(scraper_betmgm, "ingest_current_lines") as ingest,
            patch.object(scraper_betmgm, "post_discord") as discord,
            patch.object(scraper_betmgm, "BETMGM_SPORT_KEY", "baseball_mlb"),
        ):
            result = scraper_betmgm.scrape_betmgm()

        self.assertEqual(result["count"], 0)
        discord.assert_not_called()
        ingested = {call.args[2] for call in ingest.call_args_list}
        self.assertEqual(ingested, {"spreads", "h2h", "totals"})
        for call in ingest.call_args_list:
            self.assertEqual(call.args[0], "baseball_mlb")
            self.assertEqual(call.args[1], "betmgm")

    def test_a_moved_spread_alerts_discord(self):
        by_market = scraper_betmgm._build_market_lines(CDS_PAYLOAD)
        previous = {
            "2:12345_Chicago Cubs": {"line": "-1.5"},
        }
        with (
            patch.object(scraper_betmgm, "_fetch_betmgm_cds_markets", return_value=by_market),
            patch.object(scraper_betmgm, "load_previous_lines", return_value=previous),
            patch.object(scraper_betmgm, "save_current_lines"),
            patch.object(scraper_betmgm, "ingest_current_lines"),
            patch.object(scraper_betmgm, "_pinnacle_reference", return_value="n/a"),
            patch.object(scraper_betmgm, "post_discord") as discord,
        ):
            result = scraper_betmgm.scrape_betmgm()

        self.assertEqual(result["count"], 1)
        discord.assert_called_once()

    def test_no_data_returns_the_no_data_result_without_writing(self):
        with (
            patch.object(scraper_betmgm, "_fetch_betmgm_cds_markets", return_value={}),
            patch.object(scraper_betmgm, "_fetch_betmgm_direct_lines", return_value={}),
            patch.object(scraper_betmgm, "ENABLE_BROWSER_FALLBACK", False),
            patch.object(scraper_betmgm, "save_current_lines") as save,
            patch.object(scraper_betmgm, "ingest_current_lines") as ingest,
        ):
            result = scraper_betmgm.scrape_betmgm()

        self.assertEqual(result["detail"], "betmgm scrape no data")
        save.assert_not_called()
        ingest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
