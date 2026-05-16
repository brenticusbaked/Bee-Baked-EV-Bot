import unittest
from unittest.mock import patch

from execution_healthcheck import build_healthcheck


class ExecutionHealthcheckTests(unittest.TestCase):
    def test_healthcheck_requires_all_tables_to_have_rows(self):
        def fake_count(table_name):
            return 1 if table_name != "execution_fills" else 0

        def fake_latest(table_name, order_column, limit=5):
            if table_name == "venue_metrics":
                return [
                    {
                        "venue_id": "fanduel",
                        "routed_quantity": 1,
                        "filled_quantity": 1,
                        "average_fill_price": 2.0,
                        "edge_capture": 0.05,
                    }
                ]
            return [{"id": table_name, "order_column": order_column, "limit": limit}]

        with patch("execution_healthcheck.get_table_count", side_effect=fake_count), patch(
            "execution_healthcheck.get_latest_rows", side_effect=fake_latest
        ):
            result = build_healthcheck(limit=3)

        self.assertFalse(result["ok"])
        self.assertEqual(result["tables"]["execution_fills"]["count"], 0)
        self.assertEqual(result["venue_summary_from_latest_rows"][0]["venue_id"], "fanduel")


if __name__ == "__main__":
    unittest.main()
