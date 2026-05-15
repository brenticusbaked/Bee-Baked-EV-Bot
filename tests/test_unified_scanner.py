import unittest

from utils.odds import fair_probabilities_from_prices


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
