import unittest
from unittest.mock import patch

import unified_bot
from utils.odds import fair_probabilities_from_prices


class EvFloorTests(unittest.TestCase):
    def test_floor_raises_below_floor_thresholds(self):
        with patch("unified_bot.validated_ev_floor", return_value=None):
            # Spread default is 0.01 but the 0.02 floor lifts it.
            self.assertGreaterEqual(
                unified_bot._market_ev_threshold("spreads"), unified_bot.UNIFIED_EV_FLOOR
            )

    def test_history_floor_only_raises_never_lowers(self):
        with patch("unified_bot.validated_ev_floor", return_value=0.05):
            self.assertEqual(unified_bot._market_ev_threshold("spreads"), 0.05)
        with patch("unified_bot.validated_ev_floor", return_value=0.0):
            self.assertEqual(
                unified_bot._market_ev_threshold("spreads"), unified_bot.UNIFIED_EV_FLOOR
            )


class UnifiedScannerTests(unittest.TestCase):
    def test_fair_probabilities_remove_two_way_vig(self):
        probabilities = fair_probabilities_from_prices(
            {
                ("over", "8.5"): 1.91,
                ("under", "8.5"): 1.91,
            }
        )

        self.assertAlmostEqual(probabilities[("over", "8.5")], 0.5)
        self.assertAlmostEqual(probabilities[("under", "8.5")], 0.5)

    def test_fair_probabilities_pair_spread_points_by_absolute_value(self):
        probabilities = fair_probabilities_from_prices(
            {
                ("home", "-1.5"): 1.95,
                ("away", "1.5"): 1.87,
            }
        )

        self.assertAlmostEqual(sum(probabilities.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
