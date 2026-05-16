import unittest

from execution.desk import ExecutionDesk
from execution.models import ParentOrder, Side, VenueQuote
from execution.risk import RiskLimits, RiskManager
from execution.router import SmartOrderRouter


class ExecutionDeskTests(unittest.TestCase):
    def test_routes_to_best_price_then_next_liquidity(self):
        order = ParentOrder(
            symbol="Bulls @ Heat | spreads | Bulls -4.5",
            side=Side.BUY,
            quantity=3.0,
            limit_price=1.95,
            fair_price=1.86,
            metadata={"edge": 0.04},
        )
        quotes = [
            VenueQuote("slow_expensive", order.symbol, ask_price=1.95, available_quantity=3.0, latency_ms=500),
            VenueQuote("best", order.symbol, ask_price=1.90, available_quantity=1.0, latency_ms=80),
            VenueQuote("next", order.symbol, ask_price=1.92, available_quantity=5.0, latency_ms=80),
        ]

        report = ExecutionDesk.paper(quotes).execute(order)

        self.assertEqual(report.status.value, "FILLED")
        self.assertEqual([child.venue_id for child in report.child_orders], ["best", "next"])
        self.assertEqual(report.metrics["filled_quantity"], 3.0)
        self.assertLess(report.metrics["average_price"], order.limit_price)

    def test_rejects_order_below_risk_edge(self):
        order = ParentOrder(
            symbol="Bulls @ Heat | h2h | Bulls",
            side=Side.BUY,
            quantity=1.0,
            limit_price=1.80,
            fair_price=1.79,
            metadata={"edge": 0.001},
        )
        risk = RiskManager(RiskLimits(min_edge=0.02))

        report = ExecutionDesk.paper([], risk=risk).execute(order)

        self.assertEqual(report.status.value, "REJECTED")
        self.assertIn("edge", report.rejected_reason)

    def test_betting_price_mode_prefers_higher_odds(self):
        order = ParentOrder(
            symbol="Bulls @ Heat | totals | Over 210.5",
            side=Side.BUY,
            quantity=2.0,
            limit_price=2.05,
            fair_price=1.95,
            metadata={"edge": 0.05, "price_mode": "higher_is_better"},
        )
        quotes = [
            VenueQuote("lower_payout", order.symbol, ask_price=1.91, available_quantity=2.0, latency_ms=50),
            VenueQuote("higher_payout", order.symbol, ask_price=2.02, available_quantity=2.0, latency_ms=50),
        ]

        report = ExecutionDesk.paper(quotes).execute(order)

        self.assertEqual(report.child_orders[0].venue_id, "higher_payout")
        self.assertGreater(report.metrics["edge_capture"], 0)

    def test_adaptive_venue_score_can_break_near_tie(self):
        order = ParentOrder(
            symbol="Bulls @ Heat | totals | Over 210.5",
            side=Side.BUY,
            quantity=1.0,
            limit_price=2.0,
            fair_price=1.95,
            metadata={"edge": 0.03, "price_mode": "higher_is_better"},
        )
        quotes = [
            VenueQuote("flaky", order.symbol, ask_price=2.0, available_quantity=1.0, latency_ms=50),
            VenueQuote("reliable", order.symbol, ask_price=1.999, available_quantity=1.0, latency_ms=50),
        ]
        router = SmartOrderRouter(venue_scores={"flaky": 0.25, "reliable": 1.0})

        report = ExecutionDesk.paper(quotes, router=router).execute(order)

        self.assertEqual(report.child_orders[0].venue_id, "reliable")
        self.assertEqual(report.child_orders[0].metadata["venue_score"], 1.0)


if __name__ == "__main__":
    unittest.main()
