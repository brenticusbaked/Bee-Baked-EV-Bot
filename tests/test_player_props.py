import unittest
from unittest.mock import patch

import unified_bot
from utils.odds import multiplicative_unvig, decimal_implied_probability


def _prop_event():
    return {
        "id": "evt_prop",
        "home_team": "Lakers",
        "away_team": "Celtics",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {"name": "Over", "description": "LeBron James", "point": 25.5, "price": 1.72},
                            {"name": "Under", "description": "LeBron James", "point": 25.5, "price": 2.30},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {"name": "Over", "description": "LeBron James", "point": 25.5, "price": 1.95},
                            {"name": "Under", "description": "LeBron James", "point": 25.5, "price": 1.83},
                        ],
                    }
                ],
            },
        ],
    }


class PlayerPropEvaluationTests(unittest.TestCase):
    def test_multiplicative_fair_probability(self):
        # Direct check of the asymmetric-juice de-vig formula.
        implied = [decimal_implied_probability(1.72), decimal_implied_probability(2.30)]
        fair = multiplicative_unvig(implied)
        self.assertAlmostEqual(sum(fair), 1.0, places=9)
        expected_over = implied[0] / (implied[0] + implied[1])
        self.assertAlmostEqual(fair[0], expected_over, places=9)

    def test_positive_ev_over_prop_generates_alert(self):
        with patch.object(unified_bot, "is_already_logged", return_value=False), \
                patch.object(unified_bot, "log_bet_to_db", return_value=True):
            alerts = unified_bot.evaluate_player_props(
                _prop_event(),
                "basketball_nba",
                ["draftkings", "fanduel"],
                {},
            )
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["sport"], "basketball_nba")
        self.assertIn("LeBron James", alert["description"])
        self.assertIn("PLAYER PROP", alert["description"])
        self.assertIn("multiplicative", alert["description"])

    def test_no_alert_when_soft_below_threshold(self):
        event = _prop_event()
        # Soft over priced at the fair line -> no edge.
        event["bookmakers"][1]["markets"][0]["outcomes"][0]["price"] = 1.60
        event["bookmakers"][1]["markets"][0]["outcomes"][1]["price"] = 1.60
        with patch.object(unified_bot, "is_already_logged", return_value=False), \
                patch.object(unified_bot, "log_bet_to_db", return_value=True):
            alerts = unified_bot.evaluate_player_props(
                event, "basketball_nba", ["draftkings"], {},
            )
        self.assertEqual(alerts, [])

    def test_skips_group_missing_sharp_side(self):
        event = _prop_event()
        # Drop pinnacle under -> incomplete baseline, cannot de-vig.
        event["bookmakers"][0]["markets"][0]["outcomes"] = [
            event["bookmakers"][0]["markets"][0]["outcomes"][0]
        ]
        with patch.object(unified_bot, "is_already_logged", return_value=False), \
                patch.object(unified_bot, "log_bet_to_db", return_value=True):
            alerts = unified_bot.evaluate_player_props(
                event, "basketball_nba", ["draftkings"], {},
            )
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
