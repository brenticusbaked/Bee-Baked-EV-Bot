import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import unified_bot


def _future_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")


def _tennis_h2h_cache(fav_soft_price: float, dog_soft_price: float) -> dict:
    """ATP event keyed by a per-tournament sport key, moneyline only."""
    return {
        "tennis_atp_wimbledon": [
            {
                "id": "evt_tennis",
                "home_team": "Carlos Alcaraz",
                "away_team": "Jannik Sinner",
                "commence_time": _future_iso(),
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Carlos Alcaraz", "price": 1.90},
                                    {"name": "Jannik Sinner", "price": 1.90},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Carlos Alcaraz", "price": fav_soft_price},
                                    {"name": "Jannik Sinner", "price": dog_soft_price},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
    }


class TennisGatingTests(unittest.TestCase):
    def test_tennis_h2h_allowed_by_prefix(self):
        self.assertTrue(unified_bot._market_allowed_for_sport("tennis_atp_wimbledon", "h2h"))
        self.assertTrue(unified_bot._market_allowed_for_sport("tennis_wta_us_open", "h2h"))

    def test_tennis_non_h2h_markets_rejected(self):
        self.assertFalse(unified_bot._market_allowed_for_sport("tennis_atp_wimbledon", "spreads"))
        self.assertFalse(unified_bot._market_allowed_for_sport("tennis_wta_us_open", "totals"))

    def test_tennis_alerts_can_be_disabled(self):
        with patch.object(unified_bot, "ENABLE_TENNIS_ALERTS", False):
            self.assertFalse(unified_bot._market_allowed_for_sport("tennis_atp_wimbledon", "h2h"))


class TennisWebhookRoutingTests(unittest.TestCase):
    def test_tennis_routes_to_tennis_webhook(self):
        with patch.object(unified_bot, "TENNIS_ALERT_WEBHOOK", "https://hook/tennis"):
            self.assertEqual(
                unified_bot.webhook_for_sport("tennis_atp_wimbledon"),
                "https://hook/tennis",
            )

    def test_non_tennis_routing_unchanged(self):
        with patch.dict(
            unified_bot.SPORT_ALERT_WEBHOOKS,
            {"baseball_mlb": "https://hook/mlb"},
        ):
            self.assertEqual(unified_bot.webhook_for_sport("baseball_mlb"), "https://hook/mlb")


class TennisScanTests(unittest.TestCase):
    def test_tennis_h2h_edge_alerts_and_routes(self):
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
                patch.object(unified_bot, "TENNIS_ALERT_WEBHOOK", "https://hook/tennis"), \
                patch.object(unified_bot, "send_discord_alert", side_effect=_capture):
            result = unified_bot.scan_markets(cache_override=_tennis_h2h_cache(2.10, 2.10))

        self.assertEqual(result["count"], 1)
        self.assertEqual(sent["webhook_url"], "https://hook/tennis")


class EvThresholdTests(unittest.TestCase):
    def test_h2h_threshold_default_is_1_5_percent(self):
        self.assertLessEqual(unified_bot.UNIFIED_H2H_EV_THRESHOLD, 0.015)

    def test_alt_and_partial_thresholds_default_1_5_percent(self):
        self.assertLessEqual(unified_bot.UNIFIED_ALT_MARKET_EV_THRESHOLD, 0.015)
        self.assertLessEqual(unified_bot.UNIFIED_PARTIAL_MARKET_EV_THRESHOLD, 0.015)

    def test_mlb_h2h_enabled_by_default(self):
        self.assertTrue(unified_bot.ENABLE_MLB_H2H_ALERTS)


if __name__ == "__main__":
    unittest.main()
