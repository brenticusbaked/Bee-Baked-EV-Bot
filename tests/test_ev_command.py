import unittest

from utils.ev_command import compute_ev_response, format_american


class EVCommandTests(unittest.TestCase):
    def test_format_american(self):
        self.assertEqual(format_american(150), "+150")
        self.assertEqual(format_american(-110), "-110")

    def test_positive_ev_recommendation(self):
        result = compute_ev_response(my_odds=+150, pinnacle_odds_1=-160, pinnacle_odds_2=+140)
        self.assertGreater(result["ev_pct"], 0.0)
        self.assertEqual(result["recommendation"], "Fire")

    def test_negative_ev_recommendation(self):
        result = compute_ev_response(my_odds=-200, pinnacle_odds_1=-150, pinnacle_odds_2=+130)
        self.assertLess(result["ev_pct"], 0.0)
        self.assertEqual(result["recommendation"], "Pass")

    def test_marginal_ev_recommendation(self):
        # Offered odds slightly worse than fair -> small negative or near-zero edge.
        result = compute_ev_response(my_odds=-140, pinnacle_odds_1=-150, pinnacle_odds_2=+130)
        self.assertLess(result["ev_pct"], 0.02)
        self.assertNotEqual(result["recommendation"], "Fire")

    def test_zero_odds_raises(self):
        with self.assertRaises(ValueError):
            compute_ev_response(my_odds=0, pinnacle_odds_1=-110, pinnacle_odds_2=-110)


if __name__ == "__main__":
    unittest.main()
