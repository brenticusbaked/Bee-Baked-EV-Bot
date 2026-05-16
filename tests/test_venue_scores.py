import unittest

from execution.venue_scores import build_venue_scores


class VenueScoresTests(unittest.TestCase):
    def test_build_venue_scores_rewards_stronger_execution_quality(self):
        scores = build_venue_scores(
            [
                {"venue_id": "good", "routed_quantity": 1, "filled_quantity": 1, "edge_capture": 0.04, "latency_ms": 50, "fee": 0},
                {"venue_id": "good", "routed_quantity": 1, "filled_quantity": 1, "edge_capture": 0.03, "latency_ms": 50, "fee": 0},
                {"venue_id": "good", "routed_quantity": 1, "filled_quantity": 1, "edge_capture": 0.02, "latency_ms": 50, "fee": 0},
                {"venue_id": "bad", "routed_quantity": 1, "filled_quantity": 0, "edge_capture": -0.03, "latency_ms": 400, "fee": 0},
                {"venue_id": "bad", "routed_quantity": 1, "filled_quantity": 0, "edge_capture": -0.02, "latency_ms": 400, "fee": 0},
                {"venue_id": "bad", "routed_quantity": 1, "filled_quantity": 0, "edge_capture": -0.01, "latency_ms": 400, "fee": 0},
            ],
            min_sample=3,
        )

        self.assertGreater(scores["good"].score, scores["bad"].score)
        self.assertEqual(scores["good"].fill_rate, 1.0)
        self.assertEqual(scores["bad"].fill_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
