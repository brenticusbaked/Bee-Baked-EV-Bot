"""Alert lane routing: each lane prefers its own webhook and degrades through a
documented fallback chain rather than going silent.
"""

import importlib
import unittest
from unittest.mock import patch

DEFAULT = "https://discord.com/api/webhooks/default"
BET_ALERTS = "https://discord.com/api/webhooks/bet-alerts"
STATUS = "https://discord.com/api/webhooks/status"
MLB_BETS = "https://discord.com/api/webhooks/mlb-bets"
DESK = "https://discord.com/api/webhooks/honey-hammers"
RADAR = "https://discord.com/api/webhooks/honey-radar"
HOME_RUNS = "https://discord.com/api/webhooks/home-runs"


def _channels(**env):
    with patch.dict("os.environ", env, clear=True):
        return importlib.reload(importlib.import_module("services.discord_channels"))


class ChannelRoutingTests(unittest.TestCase):
    def tearDown(self):
        # Leave the module reflecting the ambient environment for other tests.
        importlib.reload(importlib.import_module("services.discord_channels"))

    def test_dedicated_lanes_win(self):
        channels = _channels(
            DISCORD_WEBHOOK_URL=DEFAULT,
            DISCORD_BET_ALERTS_WEBHOOK_URL=BET_ALERTS,
            DISCORD_STATUS_WEBHOOK_URL=STATUS,
            DISCORD_EXECUTION_DESK_WEBHOOK_URL=DESK,
            DISCORD_NEAR_MISS_WEBHOOK_URL=RADAR,
            DISCORD_HOME_RUNS_WEBHOOK_URL=HOME_RUNS,
        )
        self.assertEqual(channels.EXECUTION_DESK_WEBHOOK_URL, DESK)
        self.assertEqual(channels.NEAR_MISS_WEBHOOK_URL, RADAR)
        self.assertEqual(channels.HOME_RUNS_WEBHOOK_URL, HOME_RUNS)

    def test_fallbacks_when_the_dedicated_hook_is_unset(self):
        channels = _channels(
            DISCORD_WEBHOOK_URL=DEFAULT,
            DISCORD_BET_ALERTS_WEBHOOK_URL=BET_ALERTS,
            DISCORD_STATUS_WEBHOOK_URL=STATUS,
            DISCORD_MLB_BETS_WEBHOOK_URL=MLB_BETS,
        )
        self.assertEqual(channels.EXECUTION_DESK_WEBHOOK_URL, BET_ALERTS)
        self.assertEqual(channels.NEAR_MISS_WEBHOOK_URL, STATUS)
        self.assertEqual(channels.HOME_RUNS_WEBHOOK_URL, MLB_BETS)

    def test_default_webhook_is_the_last_resort(self):
        channels = _channels(DISCORD_WEBHOOK_URL=DEFAULT)
        self.assertEqual(channels.EXECUTION_DESK_WEBHOOK_URL, DEFAULT)
        self.assertEqual(channels.NEAR_MISS_WEBHOOK_URL, DEFAULT)
        self.assertEqual(channels.HOME_RUNS_WEBHOOK_URL, DEFAULT)

    def test_no_webhooks_configured_resolves_to_none(self):
        channels = _channels()
        self.assertIsNone(channels.EXECUTION_DESK_WEBHOOK_URL)
        self.assertIsNone(channels.NEAR_MISS_WEBHOOK_URL)
        self.assertIsNone(channels.HOME_RUNS_WEBHOOK_URL)

    def test_home_runs_lane_does_not_borrow_the_near_miss_hook(self):
        channels = _channels(DISCORD_NEAR_MISS_WEBHOOK_URL=RADAR)
        self.assertIsNone(channels.HOME_RUNS_WEBHOOK_URL)
        self.assertIsNone(channels.EXECUTION_DESK_WEBHOOK_URL)


class LaneWiringTests(unittest.TestCase):
    def test_execution_desk_posts_to_the_desk_lane(self):
        import execution_scanner
        import services.discord_channels as channels

        self.assertIs(
            execution_scanner.EXECUTION_DESK_WEBHOOK_URL,
            channels.EXECUTION_DESK_WEBHOOK_URL,
        )

    def test_near_miss_digest_posts_to_the_near_miss_lane(self):
        from test_opposite_side_suppression import _wnba_h2h_cache

        import unified_bot

        with (
            patch.object(unified_bot, "NEAR_MISS_WEBHOOK_URL", RADAR),
            patch.object(unified_bot, "get_book_weights", return_value={}),
            patch.object(unified_bot, "get_all_graded_bets", return_value=[]),
            patch.object(unified_bot, "get_today_bets", return_value=[]),
            patch.object(unified_bot, "validated_ev_floor", return_value=None),
            patch.object(unified_bot, "_market_ev_threshold", return_value=0.02),
            patch.object(unified_bot, "compute_time_decay", return_value=None),
            patch.object(unified_bot, "is_already_logged", return_value=False),
            patch.object(unified_bot, "log_bet_to_db", return_value=True),
            patch.object(unified_bot, "send_discord_alert") as send,
        ):
            unified_bot.scan_markets(cache_override=_wnba_h2h_cache(2.03, 2.03))

        digests = [
            kwargs
            for _, kwargs in send.call_args_list
            if kwargs.get("alert_type") == "near_miss_digest"
        ]
        self.assertTrue(digests)
        self.assertEqual(digests[0]["webhook_url"], RADAR)

    def test_home_run_model_posts_to_the_home_run_lane(self):
        import scraper_mlb_statcast_hr as hr_model
        import services.discord_channels as channels

        self.assertEqual(
            hr_model.DISCORD_WEBHOOK_URL,
            (channels.HOME_RUNS_WEBHOOK_URL or "").strip(),
        )


if __name__ == "__main__":
    unittest.main()
