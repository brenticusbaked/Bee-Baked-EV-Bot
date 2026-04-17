import unittest

from utils.odds import american_to_decimal, decimal_to_american, profit_for_result, quarter_kelly_units


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


if __name__ == "__main__":
    unittest.main()
