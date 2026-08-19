"""ScraperAPI transport: request shaping, retry classification, credit
accounting and the scrapers' wiring. No network access."""

import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from services import scraper_api_client as client


def _response(status_code=200, payload=None, text="", content_type="application/json"):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {"content-type": content_type}
    response.text = text
    if payload is None:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = payload
    return response


class ScraperApiClientTestCase(unittest.TestCase):
    def setUp(self):
        client.reset_run_state()
        patcher = patch.dict("os.environ", {"SCRAPERAPI_KEY": "test-key"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep_patcher = patch.object(client.time, "sleep")
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)
        credits_patcher = patch.object(client, "_record_monthly_credits")
        credits_patcher.start()
        self.addCleanup(credits_patcher.stop)
        self.addCleanup(client.reset_run_state)


class RequestShapeTests(ScraperApiClientTestCase):
    def test_json_feed_books_pay_for_ips_not_for_rendering(self):
        options = client.options_for("draftkings")
        self.assertTrue(options.premium)
        self.assertFalse(options.render)
        # 10 credits for premium alone; rendering as well would be 25.
        self.assertEqual(options.credits, 10)

    def test_render_can_be_escalated_per_book_without_a_deploy(self):
        with patch.dict("os.environ", {"SCRAPERAPI_RENDER_BOOKS": "betmgm"}, clear=False):
            self.assertTrue(client.options_for("betmgm").render)
            self.assertEqual(client.options_for("betmgm").credits, 25)
            self.assertFalse(client.options_for("draftkings").render)

    def test_ultra_premium_replaces_premium_rather_than_stacking(self):
        with patch.dict("os.environ", {"SCRAPERAPI_ULTRA_BOOKS": "fanatics"}, clear=False):
            options = client.options_for("fanatics")
        self.assertTrue(options.ultra_premium)
        self.assertFalse(options.premium)
        self.assertEqual(options.credits, 30)

    def test_incompatible_option_combinations_are_rejected(self):
        with self.assertRaises(ValueError):
            client.ScraperApiOptions(premium=True, ultra_premium=True)
        with self.assertRaises(ValueError):
            # ScraperAPI silently drops custom headers on ultra_premium.
            client.ScraperApiOptions(premium=False, ultra_premium=True, keep_headers=True)
        with self.assertRaises(ValueError):
            client.ScraperApiOptions(wait_for_selector="#odds")

    def test_target_query_string_travels_inside_the_url_parameter(self):
        url = client.target_url("https://sbapi.example/api/page", {"page": "CUSTOM", "_ak": "abc"})
        self.assertEqual(url, "https://sbapi.example/api/page?page=CUSTOM&_ak=abc")
        self.assertEqual(
            client.target_url("https://x.example/a?b=1", {"c": "2"}),
            "https://x.example/a?b=1&c=2",
        )

    def test_params_are_built_from_the_resolved_options(self):
        params = client.build_params(
            "https://book.example/odds",
            client.ScraperApiOptions(premium=True, render=True, keep_headers=True),
            "test-key",
        )
        self.assertEqual(params["url"], "https://book.example/odds")
        self.assertEqual(params["api_key"], "test-key")
        self.assertEqual(params["premium"], "true")
        self.assertEqual(params["render"], "true")
        self.assertEqual(params["keep_headers"], "true")
        self.assertNotIn("ultra_premium", params)

    def test_fetch_sends_the_target_as_a_parameter_of_the_api_endpoint(self):
        with patch.object(client.requests, "get", return_value=_response(payload={"ok": True})) as get:
            payload = client.fetch_json("https://book.example/odds", "draftkings")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(get.call_args.args[0], client.API_ENDPOINT)
        self.assertEqual(get.call_args.kwargs["params"]["url"], "https://book.example/odds")

    def test_custom_headers_are_only_sent_when_keep_headers_is_set(self):
        headers = {"Referer": "https://book.example/"}
        options = replace(client.options_for("fanduel"), keep_headers=True)
        with patch.object(client.requests, "get", return_value=_response(payload={})) as get:
            client.fetch("https://book.example/odds", "fanduel", options=options, headers=headers)
        self.assertEqual(get.call_args.kwargs["headers"], headers)

        with patch.object(client.requests, "get", return_value=_response(payload={})) as get:
            client.fetch("https://book.example/odds", "fanduel", headers=headers)
        self.assertIsNone(get.call_args.kwargs["headers"])
        self.assertNotIn("keep_headers", get.call_args.kwargs["params"])


class ProxyModeTests(ScraperApiClientTestCase):
    def test_proxy_username_carries_the_request_flags(self):
        proxy = client.playwright_proxy("draftkings")
        self.assertEqual(proxy["server"], "http://proxy-server.scraperapi.com:8001")
        self.assertEqual(proxy["username"], "scraperapi.premium=true.country_code=us")
        self.assertEqual(proxy["password"], "test-key")

    def test_render_is_dropped_in_proxy_mode(self):
        # We drive our own browser there, so paying ScraperAPI to render as well
        # would bill 25 credits for a page we render locally anyway.
        with patch.dict("os.environ", {"SCRAPERAPI_RENDER_BOOKS": "betmgm"}, clear=False):
            self.assertNotIn("render", client.playwright_proxy("betmgm")["username"])

    def test_proxy_helpers_return_nothing_without_a_key(self):
        with patch.dict("os.environ", {"SCRAPERAPI_KEY": "", "SCRAPER_API_KEY": ""}, clear=False):
            self.assertIsNone(client.playwright_proxy("fanduel"))
            self.assertEqual(client.requests_proxies("fanduel"), {})

    def test_requests_proxy_mode_skips_certificate_verification(self):
        # ScraperAPI presents its own certificate for the tunnelled host.
        kwargs = client.requests_proxies("fanduel")
        self.assertFalse(kwargs["verify"])
        self.assertIn("proxy-server.scraperapi.com:8001", kwargs["proxies"]["https"])

    def test_legacy_secret_name_still_works(self):
        with patch.dict("os.environ", {"SCRAPERAPI_KEY": "", "SCRAPER_API_KEY": "legacy"}, clear=False):
            self.assertEqual(client.api_key(), "legacy")


class RetryClassificationTests(ScraperApiClientTestCase):
    def test_scraperapi_side_failures_are_retried(self):
        responses = [_response(500), _response(429), _response(payload={"ok": True})]
        with patch.object(client.requests, "get", side_effect=responses) as get:
            self.assertEqual(client.fetch_json("https://book.example", "betmgm"), {"ok": True})
        self.assertEqual(get.call_count, 3)
        stats = dict(client.stats_snapshot())["betmgm"]
        self.assertEqual(stats.retries, 2)
        self.assertEqual(stats.successes, 1)
        # Only the successful request is billed.
        self.assertEqual(stats.credits, 10)

    def test_out_of_credits_is_not_retried_and_stops_the_whole_run(self):
        with (
            patch.object(client.requests, "get", return_value=_response(403)) as get,
            self.assertRaises(client.ScraperApiOutOfCredits),
        ):
            client.fetch("https://book.example", "betmgm")
        self.assertEqual(get.call_count, 1)
        self.assertIsNotNone(client.disabled_reason())
        # A second book must not spend runner minutes rediscovering this.
        with patch.object(client.requests, "get") as get, self.assertRaises(client.ScraperApiError):
            client.fetch("https://other.example", "fanduel")
        get.assert_not_called()

    def test_invalid_key_is_not_retried(self):
        with (
            patch.object(client.requests, "get", return_value=_response(401)) as get,
            self.assertRaises(client.ScraperApiError),
        ):
            client.fetch("https://book.example", "fanduel")
        self.assertEqual(get.call_count, 1)

    def test_persistent_block_gives_up_after_the_attempt_cap(self):
        with (
            patch.object(client.requests, "get", return_value=_response(500)) as get,
            self.assertRaises(client.ScraperApiBlocked),
        ):
            client.fetch("https://book.example", "novig")
        self.assertEqual(get.call_count, client.MAX_ATTEMPTS)
        self.assertEqual(dict(client.stats_snapshot())["novig"].blocks, 1)

    def test_missing_key_raises_before_any_request(self):
        with (
            patch.dict("os.environ", {"SCRAPERAPI_KEY": "", "SCRAPER_API_KEY": ""}, clear=False),
            patch.object(client, "_alert_once") as alert,
            patch.object(client.requests, "get") as get,
            self.assertRaises(client.ScraperApiNotConfigured),
        ):
            client.fetch("https://book.example", "fanduel")
        get.assert_not_called()
        alert.assert_called_once()

    def test_html_served_instead_of_json_counts_as_a_block(self):
        response = _response(200, payload=None, text="<html>Access Denied</html>", content_type="text/html")
        with patch.object(client.requests, "get", return_value=response), self.assertRaises(client.ScraperApiBlocked):
            client.fetch_json("https://book.example", "fanatics")

    def test_try_fetch_json_swallows_transport_errors(self):
        with patch.object(client.requests, "get", return_value=_response(500)):
            self.assertIsNone(client.try_fetch_json("https://book.example", "fanatics"))


class RunBudgetTests(ScraperApiClientTestCase):
    def test_per_run_credit_cap_stops_further_requests(self):
        with patch.object(client, "MAX_CREDITS_PER_RUN", 10):
            with patch.object(client.requests, "get", return_value=_response(payload={})) as get:
                client.fetch("https://book.example/1", "draftkings")
                with self.assertRaises(client.ScraperApiError):
                    client.fetch("https://book.example/2", "draftkings")
            self.assertEqual(get.call_count, 1)

    def test_wall_clock_budget_stops_further_requests(self):
        with patch.object(client, "RUN_BUDGET_SECONDS", 0.0), patch.object(client, "_elapsed", return_value=1.0):
            with patch.object(client.requests, "get") as get, self.assertRaises(client.ScraperApiError):
                client.fetch("https://book.example", "draftkings")
            get.assert_not_called()

    def test_stats_report_covers_success_rate_and_credit_burn(self):
        with patch.object(client.requests, "get", return_value=_response(payload={})):
            client.fetch("https://book.example", "draftkings")
        with patch.object(client.requests, "get", return_value=_response(500)), self.assertRaises(client.ScraperApiBlocked):
            client.fetch("https://book.example", "fanduel")
        with patch.object(client, "monthly_credits", return_value=250):
            report = client.format_stats()
        self.assertIn("draftkings: 1/1 ok (100%)", report)
        self.assertIn("fanduel: 0/3 ok (0%)", report)
        self.assertIn("10 credits", report)
        self.assertIn("250 this month", report)


class ScraperWiringTests(ScraperApiClientTestCase):
    def test_book_scrapers_resolve_their_proxy_through_the_client(self):
        import scraper_betmgm
        import scraper_draftkings
        import scraper_fanduel

        for module in (scraper_fanduel, scraper_betmgm):
            with self.subTest(module=module.__name__):
                self.assertEqual(module._proxy_settings("scraperapi"), client.playwright_proxy(module.BOOK_KEY))
        self.assertEqual(scraper_draftkings.BOOK_KEY, "draftkings")

    def test_draftkings_json_feed_goes_through_scraper_api(self):
        import scraper_draftkings

        payload = {"events": [], "eventGroup": {"events": [], "offerCategories": []}}
        with (
            patch.object(scraper_draftkings, "fetch") as fetch,
            patch.object(scraper_draftkings, "_looks_like_direct_dk_payload", return_value=True),
            patch.object(scraper_draftkings, "_normalize_direct_dk_payload", return_value=payload),
        ):
            fetch.return_value = _response(payload=payload)
            self.assertEqual(scraper_draftkings._fetch_dk_direct_payload(), payload)
        self.assertEqual(fetch.call_args.args[1], "draftkings")
        self.assertTrue(fetch.call_args.kwargs["options"].keep_headers)

    def test_fanduel_content_endpoint_goes_through_scraper_api(self):
        import scraper_fanduel

        with (
            patch.object(scraper_fanduel, "try_fetch_json", return_value={"attachments": {}}) as fetch,
            patch.object(scraper_fanduel, "_looks_like_fanduel_nba_payload", return_value=True),
        ):
            self.assertIsNotNone(scraper_fanduel._fetch_fanduel_direct_payload())
        url = fetch.call_args.args[0]
        self.assertTrue(url.startswith(scraper_fanduel.FANDUEL_CONTENT_API))
        self.assertIn("customPageId=nba", url)
        self.assertEqual(fetch.call_args.args[1], "fanduel")


if __name__ == "__main__":
    unittest.main()
