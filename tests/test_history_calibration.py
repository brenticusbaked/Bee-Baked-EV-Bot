import unittest
from unittest.mock import patch

import services.history_calibration as hc
from services.book_weights import book_weight_for


SUMMARY = {
    "overall": {"n": 1000, "stake": 1000.0, "profit": -50.0, "roi": -0.05},
    "by_book": {
        # Big sample, strongly negative ROI -> weight below 1.0
        "draftkings": {"n": 4000, "stake": 4000.0, "profit": -400.0, "roi": -0.10},
        # Big sample, positive ROI -> weight above 1.0
        "bet365": {"n": 900, "stake": 900.0, "profit": 36.0, "roi": 0.04},
        # Below min sample -> ignored
        "fliff": {"n": 10, "stake": 100.0, "profit": -50.0, "roi": -0.50},
    },
    "ev_buckets": {
        "neg": {"n": 5000, "roi": -0.09},
        "0-2%": {"n": 2000, "roi": 0.009},
        "2-5%": {"n": 2000, "roi": 0.03},
    },
    "clv_by_book": {
        "draftkings": {"n": 3000, "avg_clv_pct": 0.77, "pct_beat_close": 0.45},
    },
    "by_type": {
        # Big sample, profitable -> negative delta (threshold nudged down)
        "batter_total_bases": {"n": 800, "roi": 0.06},
        # Big sample, losing -> positive delta (threshold nudged up)
        "pitcher_strikeouts": {"n": 800, "roi": -0.08},
        # Below min sample -> ignored (neutral)
        "batter_home_runs": {"n": 10, "roi": 0.50},
    },
}


class TestHistoryCalibration(unittest.TestCase):
    def setUp(self):
        hc.reset_cache()
        self.addCleanup(hc.reset_cache)

    def test_book_factors_shrink_and_clamp(self):
        with patch("services.history_calibration.load_summary", return_value=SUMMARY):
            factors = hc.history_book_factors()
        self.assertLess(factors["draftkings"], 1.0)
        self.assertGreater(factors["bet365"], 1.0)
        self.assertNotIn("fliff", factors)  # below MIN_BOOK_SAMPLE

    def test_validated_floor_lowest_profitable_bucket(self):
        with patch("services.history_calibration.load_summary", return_value=SUMMARY):
            self.assertEqual(hc.validated_ev_floor(), 0.0)

    def test_neutral_without_summary(self):
        with patch("services.history_calibration.load_summary", return_value=None):
            self.assertEqual(hc.history_book_factors(), {})
            self.assertIsNone(hc.validated_ev_floor())
            self.assertEqual(hc.book_factor_for("FanDuel"), 1.0)

    def test_clv_baseline_lookup_by_title(self):
        with patch("services.history_calibration.load_summary", return_value=SUMMARY):
            self.assertAlmostEqual(hc.clv_baseline_for("Draftkings Sportsbook"), 0.77)
            self.assertIsNone(hc.clv_baseline_for("Novig"))

    def test_prop_type_ev_adjustment_sign_and_bounds(self):
        with patch("services.history_calibration.load_summary", return_value=SUMMARY):
            profitable = hc.prop_type_ev_adjustment("batter_total_bases")
            losing = hc.prop_type_ev_adjustment("pitcher_strikeouts")
            small = hc.prop_type_ev_adjustment("batter_home_runs")
            unknown = hc.prop_type_ev_adjustment("pitcher_outs")
        # Profitable type lowers the threshold (negative), loser raises it.
        self.assertLess(profitable, 0.0)
        self.assertGreater(losing, 0.0)
        # Clamped within the configured cap.
        self.assertGreaterEqual(profitable, -hc.MAX_PROP_TYPE_EV_ADJUST)
        self.assertLessEqual(losing, hc.MAX_PROP_TYPE_EV_ADJUST)
        # Below-sample and unknown types are neutral.
        self.assertEqual(small, 0.0)
        self.assertEqual(unknown, 0.0)

    def test_prop_type_adjustment_neutral_without_summary(self):
        with patch("services.history_calibration.load_summary", return_value=None):
            self.assertEqual(hc.history_prop_type_ev_deltas(), {})
            self.assertEqual(hc.prop_type_ev_adjustment("batter_total_bases"), 0.0)

    def test_prop_type_case_insensitive_lookup(self):
        with patch("services.history_calibration.load_summary", return_value=SUMMARY):
            self.assertLess(hc.prop_type_ev_adjustment("BATTER_TOTAL_BASES"), 0.0)

    def test_book_weight_for_applies_history_when_missing(self):
        with patch("services.history_calibration.load_summary", return_value=SUMMARY):
            hc.reset_cache()
            weight = book_weight_for({}, "DraftKings")
        self.assertLess(weight, 1.0)


if __name__ == "__main__":
    unittest.main()
