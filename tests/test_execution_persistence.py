import unittest
from datetime import datetime

from db_manager import _execution_payload_from_report
from execution.desk import ExecutionDesk, report_to_dict
from execution.models import ParentOrder, Side, VenueQuote


class ExecutionPersistenceTests(unittest.TestCase):
    def test_execution_report_maps_to_supabase_payloads(self):
        order = ParentOrder(
            symbol="Bulls @ Heat | spreads | Bulls -4.5",
            side=Side.BUY,
            quantity=2.0,
            limit_price=2.00,
            fair_price=1.90,
            source_signal="test_signal",
            metadata={"edge": 0.05, "price_mode": "higher_is_better"},
        )
        report = report_to_dict(
            ExecutionDesk.paper(
                [
                    VenueQuote("fanduel", order.symbol, ask_price=2.00, available_quantity=1.0),
                    VenueQuote("draftkings", order.symbol, ask_price=1.98, available_quantity=1.0),
                ]
            ).execute(order)
        )

        payload = _execution_payload_from_report(report)

        self.assertEqual(payload["order"]["order_id"], order.order_id)
        self.assertEqual(payload["order"]["source_signal"], "test_signal")
        self.assertEqual(len(payload["child_orders"]), 2)
        self.assertEqual(len(payload["fills"]), 2)
        self.assertEqual(len(payload["venue_metrics"]), 2)
        self.assertTrue(payload["fills"][0]["fill_id"].startswith(payload["fills"][0]["child_order_id"]))
        self.assertTrue(payload["venue_metrics"][0]["metric_id"].startswith(payload["venue_metrics"][0]["child_order_id"]))
        self.assertFalse(_contains_datetime(payload))


def _contains_datetime(value):
    if isinstance(value, datetime):
        return True
    if isinstance(value, dict):
        return any(_contains_datetime(inner) for inner in value.values())
    if isinstance(value, list):
        return any(_contains_datetime(inner) for inner in value)
    return False


if __name__ == "__main__":
    unittest.main()
