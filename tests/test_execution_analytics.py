import unittest

from execution_analytics import summarize_venue_metrics


class ExecutionAnalyticsTests(unittest.TestCase):
    def test_summarizes_venue_metrics(self):
        summary = summarize_venue_metrics(
            [
                {
                    "venue_id": "fanduel",
                    "routed_quantity": 2,
                    "filled_quantity": 1,
                    "average_fill_price": 2.0,
                    "fee": 0,
                    "edge_capture": 0.05,
                },
                {
                    "venue_id": "fanduel",
                    "routed_quantity": 1,
                    "filled_quantity": 1,
                    "average_fill_price": 1.9,
                    "fee": 0,
                    "edge_capture": 0.03,
                },
            ]
        )

        self.assertEqual(summary[0]["venue_id"], "fanduel")
        self.assertEqual(summary[0]["orders"], 2)
        self.assertAlmostEqual(summary[0]["fill_rate"], 0.666667)
        self.assertAlmostEqual(summary[0]["average_fill_price"], 1.95)
        self.assertAlmostEqual(summary[0]["average_edge_capture"], 0.04)


if __name__ == "__main__":
    unittest.main()
