import unittest
from unittest import mock
from unittest.mock import patch

import continuous_scan
import unified_bot
try:
    # `unittest discover -s tests` puts the tests dir on sys.path, so the bare
    # import works. Prefer it: a dependency in site-packages can ship its own
    # top-level `tests` package that shadows this repo's `tests` namespace.
    from test_opposite_side_suppression import _wnba_h2h_cache
except ImportError:
    from tests.test_opposite_side_suppression import _wnba_h2h_cache


class ScanMarketsRoutingTests(unittest.TestCase):
    def test_webhook_override_and_prefix_applied(self):
        sent = {}

        def _capture(payload, **kwargs):
            sent["description"] = payload["embeds"][0]["description"]
            sent["webhook_url"] = kwargs.get("webhook_url")
            return None

        with patch.object(unified_bot, "get_book_weights", return_value={}), \
                patch.object(unified_bot, "get_all_graded_bets", return_value=[]), \
                patch.object(unified_bot, "get_today_bets", return_value=[]), \
                patch.object(unified_bot, "validated_ev_floor", return_value=None), \
                patch.object(unified_bot, "is_already_logged", return_value=False), \
                patch.object(unified_bot, "log_bet_to_db", return_value=True), \
                patch.object(unified_bot, "send_discord_alert", side_effect=_capture):
            result = unified_bot.scan_markets(
                cache_override=_wnba_h2h_cache(2.10, 2.10),
                alert_prefix="🌙 **[OPENER]**\n\n",
                webhook_override="https://hook/opener",
            )

        self.assertEqual(result["count"], 1)
        self.assertEqual(sent["webhook_url"], "https://hook/opener")
        self.assertTrue(sent["description"].startswith("🌙 **[OPENER]**"))


class OpenerScanTests(unittest.TestCase):
    def test_opener_scan_routes_to_opener_stream_with_prefix(self):
        cache = {"basketball_wnba": [{"id": "e1"}]}
        with mock.patch.object(continuous_scan, "get_market_cache", return_value=cache), \
             mock.patch.object(continuous_scan, "OPENER_WEBHOOK_URL", "https://hook/opener"), \
             mock.patch.object(continuous_scan, "scan_markets", return_value={"count": 0}) as scan:
            continuous_scan.run_opener_scan()

        _, kwargs = scan.call_args
        self.assertEqual(kwargs["source"], "opener_scan")
        self.assertEqual(kwargs["alert_type"], "opener_alert")
        self.assertEqual(kwargs["webhook_override"], "https://hook/opener")
        self.assertIn("OPENER", kwargs["alert_prefix"])

    def test_continuous_scan_uses_bet_alert_stream(self):
        cache = {"basketball_wnba": [{"id": "e1"}]}
        with mock.patch.object(continuous_scan, "get_market_cache", return_value=cache), \
             mock.patch.object(continuous_scan, "scan_markets", return_value={"count": 0}) as scan:
            continuous_scan.run_continuous_scan()

        _, kwargs = scan.call_args
        self.assertEqual(kwargs["source"], "continuous_scan")
        self.assertEqual(kwargs["alert_type"], "bet_alert")
        self.assertNotIn("webhook_override", kwargs)

    def test_opener_scan_empty_cache_short_circuits(self):
        with mock.patch.object(continuous_scan, "get_market_cache", return_value={}), \
             mock.patch.object(continuous_scan, "scan_markets") as scan:
            result = continuous_scan.run_opener_scan()

        scan.assert_not_called()
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
