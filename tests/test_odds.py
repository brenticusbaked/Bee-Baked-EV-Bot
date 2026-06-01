import unittest

from utils.odds import (
    american_to_decimal,
    decimal_to_american,
    devig_probabilities,
    fair_probabilities_from_prices,
    multiplicative_unvig,
    power_unvig,
    profit_for_result,
    quarter_kelly_units,
)


class OddsTests(unittest.TestCase):
    def test_decimal_to_american_positive(self):
        self.assertEqual(decimal_to_american(2.5), "+150")

    def test_decimal_to_american_negative(self):
        self.assertEqual(decimal_to_american(1.5), "-200")

    def test_american_to_decimal(self):
        self.assertAlmostEqual(american_to_decimal("+150"), 2.5)
        self.assertAlmostEqual(american_to_decimal("-200"), 1.5)

    def test_profit_for_result(self):
        self.assertAlmostEqual(profit_for_result("+150", 2, "WIN"), 3.0)
        self.assertAlmostEqual(profit_for_result("-200", 2, "WIN"), 1.0)
        self.assertAlmostEqual(profit_for_result("+150", 2, "LOSS"), -2.0)

    def test_quarter_kelly_units_capped_and_non_negative(self):
        self.assertEqual(quarter_kelly_units(-0.1, 2.1), 0.0)
        self.assertEqual(quarter_kelly_units(1.0, 1.1), 5.0)

    def test_multiplicative_unvig_normalizes_overround(self):
        fair = multiplicative_unvig([0.55, 0.55])
        self.assertAlmostEqual(sum(fair), 1.0)
        self.assertAlmostEqual(fair[0], 0.5)

    def test_power_unvig_normalizes_overround(self):
        fair = power_unvig([0.60, 0.50])
        self.assertAlmostEqual(sum(fair), 1.0, places=8)
        self.assertGreater(fair[0], fair[1])

    def test_devig_probabilities_dispatches_multiplicative(self):
        fair = devig_probabilities([0.60, 0.50], method="multiplicative")
        self.assertAlmostEqual(fair[0], 0.60 / 1.10)
        self.assertAlmostEqual(sum(fair), 1.0)

    def test_fair_probabilities_from_prices_supports_power_method(self):
        prices = {("team a", ""): 1.80, ("team b", ""): 2.10}
        fair = fair_probabilities_from_prices(prices, method="power")
        self.assertEqual(set(fair), set(prices))
        self.assertAlmostEqual(sum(fair.values()), 1.0, places=8)


if __name__ == "__main__":
    unittest.main()
