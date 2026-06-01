import unittest
from unittest.mock import patch

from services.live_edges import find_live_edge_alerts, send_live_edge_alerts


def event_with_prices(event_id, sharp_a, sharp_b, soft_a):
    return {
        "id": event_id,
        "home_team": "Miami Heat",
        "away_team": "Chicago Bulls",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Miami Heat", "price": sharp_a},
                            {"name": "Chicago Bulls", "price": sharp_b},
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
                        "outcomes": [{"name": "Miami Heat", "price": soft_a}],
                    }
                ],
            },
        ],
    }


class LiveEdgesTests(unittest.TestCase):
    def test_stale_high_edge_routes_to_live_hammer(self):
        previous = {"basketball_nba": [event_with_prices("evt_1", 2.0, 2.0, 2.05)]}
        current = {"basketball_nba": [event_with_prices("evt_1", 1.80, 2.20, 2.05)]}
        updated = {"basketball_nba": [event_with_prices("evt_1", 1.80, 2.20, 2.05)]}

        alerts = find_live_edge_alerts(previous, current, updated)

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["lane"], "hammer")
        self.assertIn("LIVE HAMMER", alerts[0]["payload"]["embeds"][0]["description"])
        self.assertIn("Stale Score", alerts[0]["payload"]["embeds"][0]["description"])

    def test_positive_edge_without_stale_signal_routes_to_watchlist(self):
        current = {"basketball_nba": [event_with_prices("evt_1", 1.80, 2.20, 2.05)]}
        updated = {"basketball_nba": [event_with_prices("evt_1", 1.80, 2.20, 2.05)]}

        alerts = find_live_edge_alerts({}, current, updated)

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["lane"], "watchlist")
        self.assertIn("WATCHLIST", alerts[0]["payload"]["embeds"][0]["description"])

    def test_send_live_edge_alerts_dedupes_in_memory(self):
        current = {"basketball_nba": [event_with_prices("evt_1", 1.80, 2.20, 2.05)]}
        alerts = find_live_edge_alerts({}, current, current)
        sent_keys = set()

        with patch("services.live_edges.send_discord_alert", return_value=True) as fake_send:
            first = send_live_edge_alerts(alerts, sent_dedupe_keys=sent_keys)
            second = send_live_edge_alerts(alerts, sent_dedupe_keys=sent_keys)

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(fake_send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
