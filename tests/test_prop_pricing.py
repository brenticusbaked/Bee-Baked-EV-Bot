import unittest

from utils.prop_pricing import (
    consensus_probabilities,
    infer_mean_from_over_probability,
    infer_negative_binomial_mean_from_over_probability,
    negative_binomial_prop_probabilities,
    no_vig_binary_probabilities,
    poisson_prop_probabilities,
    prop_kelly_units,
    uncertainty_adjusted_prop_kelly_units,
)


class PropPricingTests(unittest.TestCase):
    def test_no_vig_binary_probabilities_handles_asymmetric_juice(self):
        probabilities = no_vig_binary_probabilities(1.7142857, 2.10, method="multiplicative")
        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=8)
        self.assertGreater(probabilities["over"], probabilities["under"])

    def test_consensus_probabilities_averages_books(self):
        probabilities = consensus_probabilities(
            [
                {"over": {"price": 1.80}, "under": {"price": 2.00}},
                {"over": {"price": 1.90}, "under": {"price": 1.90}},
            ]
        )
        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=8)
        self.assertIn("over", probabilities)
        self.assertIn("under", probabilities)

    def test_poisson_prop_probabilities(self):
        probabilities = poisson_prop_probabilities(2.5, 3.2)
        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=8)
        self.assertGreater(probabilities["over"], probabilities["under"])

    def test_infer_mean_from_over_probability(self):
        mean = infer_mean_from_over_probability(2.5, 0.62)
        self.assertIsNotNone(mean)
        probabilities = poisson_prop_probabilities(2.5, mean)
        self.assertAlmostEqual(probabilities["over"], 0.62, places=4)

    def test_negative_binomial_prop_probabilities(self):
        probabilities = negative_binomial_prop_probabilities(8.5, 9.4, variance_multiplier=1.5)
        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=8)
        self.assertGreater(probabilities["over"], 0.0)
        self.assertLess(probabilities["over"], 1.0)

    def test_infer_negative_binomial_mean_from_over_probability(self):
        mean = infer_negative_binomial_mean_from_over_probability(8.5, 0.56, variance_multiplier=1.4)
        self.assertIsNotNone(mean)
        probabilities = negative_binomial_prop_probabilities(8.5, mean, variance_multiplier=1.4)
        self.assertAlmostEqual(probabilities["over"], 0.56, places=4)

    def test_prop_kelly_units_scales_below_quarter_kelly(self):
        eighth_kelly = prop_kelly_units(0.04, 1.95, fraction=0.125, cap=2.0)
        quarter_kelly = prop_kelly_units(0.04, 1.95, fraction=0.25, cap=2.0)
        self.assertLess(eighth_kelly, quarter_kelly)

    def test_uncertainty_adjusted_kelly_scales_down_low_confidence(self):
        high_conf_units, high_conf_edge, _ = uncertainty_adjusted_prop_kelly_units(
            0.56, 1.95, confidence=1.0, fraction=0.125, cap=2.0
        )
        low_conf_units, low_conf_edge, _ = uncertainty_adjusted_prop_kelly_units(
            0.56, 1.95, confidence=0.33, fraction=0.125, cap=2.0
        )

        self.assertGreater(high_conf_edge, low_conf_edge)
        self.assertGreater(high_conf_units, low_conf_units)


if __name__ == "__main__":
    unittest.main()
