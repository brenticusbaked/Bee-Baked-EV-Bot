import math
import unittest
from unittest import mock

import execution_scanner
from execution_scanner import (
    _execution_desk_alert_description,
    _execution_dedup_key,
    _outcome_key,
    _selection_text,
    _send_execution_desk_alerts,
    _synthetic_calibration_report,
    _uncapped_env_float,
)


def _candidate(calibration: bool = False) -> dict:
    candidate = {
        "edge": 0.145,
        "sport": "basketball_wnba",
        "event_id": "evt1",
        "matchup": "Las Vegas Aces @ Toronto Tempo",
        "market_type": "player_assists",
        "best": {"selection": "Under 6.5", "price": 2.1, "book": "BetRivers"},
        "fair_decimal": 1.83,
        "units": 1.25,
    }
    if calibration:
        candidate["calibration"] = True
    return candidate


class ExecutionScannerTests(unittest.TestCase):
    def test_synthetic_calibration_report_is_tagged_and_filled(self):
        report = _synthetic_calibration_report()

        self.assertEqual(report["status"], "FILLED")
        self.assertEqual(report["parent_order"]["source_signal"], "synthetic_calibration")
        self.assertTrue(report["parent_order"]["metadata"]["calibration"])
        self.assertTrue(report["parent_order"]["metadata"]["synthetic"])
        self.assertEqual(report["child_orders"][0]["venue_id"], "paper_healthcheck")

    def test_alert_description_includes_prop_edge_and_fair_value(self):
        description = _execution_desk_alert_description(_candidate())
        self.assertIn("EXECUTION DESK EDGE - PLAYER_ASSISTS", description)
        self.assertIn("Under 6.5", description)
        self.assertIn("14.50%", description)
        self.assertIn("Fair Value (Pinnacle)", description)

    def test_prop_selection_text_includes_player_name(self):
        outcome = {"name": "Under", "point": 6.5, "description": "Chelsea Gray"}
        self.assertEqual(_selection_text(outcome), "Chelsea Gray Under 6.5")

    def test_main_market_selection_text_has_no_player_prefix(self):
        outcome = {"name": "Las Vegas Aces", "point": -6.5}
        self.assertEqual(_selection_text(outcome), "Las Vegas Aces -6.5")

    def test_outcome_key_separates_players_sharing_a_line(self):
        gray = {"name": "Over", "point": 6.5, "description": "Chelsea Gray"}
        wilson = {"name": "Over", "point": 6.5, "description": "A'ja Wilson"}
        self.assertNotEqual(_outcome_key(gray), _outcome_key(wilson))

    def test_alerts_skip_calibration_and_respect_toggle(self):
        with mock.patch.object(execution_scanner, "ENABLE_EXECUTION_DESK_ALERTS", True), \
             mock.patch.object(execution_scanner, "EXECUTION_DESK_WEBHOOK_URL", "https://hook"), \
             mock.patch.object(execution_scanner, "send_discord_alert", return_value=True) as send:
            sent = _send_execution_desk_alerts([_candidate(), _candidate(calibration=True)])
        self.assertEqual(sent, 1)
        self.assertEqual(send.call_count, 1)

    def test_dedup_key_is_stable_across_price_moves(self):
        first = _candidate()
        second = _candidate()
        second["best"]["price"] = 2.4
        second["edge"] = 0.20
        self.assertEqual(_execution_dedup_key(first), _execution_dedup_key(second))

    def test_dedup_key_differs_by_selection(self):
        over = _candidate()
        over["best"]["selection"] = "Over 6.5"
        self.assertNotEqual(_execution_dedup_key(_candidate()), _execution_dedup_key(over))

    def test_already_sent_alert_is_suppressed(self):
        with mock.patch.object(execution_scanner, "ENABLE_EXECUTION_DESK_ALERTS", True), \
             mock.patch.object(execution_scanner, "EXECUTION_DESK_WEBHOOK_URL", "https://hook"), \
             mock.patch.object(execution_scanner, "alert_already_sent", return_value=True), \
             mock.patch.object(execution_scanner, "send_discord_alert", return_value=True) as send:
            sent = _send_execution_desk_alerts([_candidate()])
        self.assertEqual(sent, 0)
        send.assert_not_called()

    def test_fresh_alert_is_sent_with_stable_dedupe_key(self):
        with mock.patch.object(execution_scanner, "ENABLE_EXECUTION_DESK_ALERTS", True), \
             mock.patch.object(execution_scanner, "EXECUTION_DESK_WEBHOOK_URL", "https://hook"), \
             mock.patch.object(execution_scanner, "alert_already_sent", return_value=False), \
             mock.patch.object(execution_scanner, "send_discord_alert", return_value=True) as send:
            candidate = _candidate()
            sent = _send_execution_desk_alerts([candidate])
        self.assertEqual(sent, 1)
        self.assertEqual(send.call_args.kwargs["dedupe_key"], _execution_dedup_key(candidate))

    def test_alerts_disabled_sends_nothing(self):
        with mock.patch.object(execution_scanner, "ENABLE_EXECUTION_DESK_ALERTS", False), \
             mock.patch.object(execution_scanner, "send_discord_alert") as send:
            sent = _send_execution_desk_alerts([_candidate()])
        self.assertEqual(sent, 0)
        send.assert_not_called()


class UncappedEnvFloatTests(unittest.TestCase):
    def test_zero_and_sentinels_mean_unlimited(self):
        for token in ("0", "", "none", "unlimited", "-1", "inf"):
            with mock.patch.dict("os.environ", {"CAP": token}, clear=False):
                self.assertEqual(_uncapped_env_float("CAP", 5.0), math.inf)

    def test_positive_value_is_used(self):
        with mock.patch.dict("os.environ", {"CAP": "12.5"}, clear=False):
            self.assertEqual(_uncapped_env_float("CAP", 5.0), 12.5)

    def test_unset_uses_default(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CAP", None)
            self.assertEqual(_uncapped_env_float("CAP", 5.0), 5.0)


class RiskManagerUncappedTests(unittest.TestCase):
    def test_large_order_accepted_with_infinite_limits(self):
        from execution.models import ParentOrder, Side
        from execution.risk import RiskLimits, RiskManager

        risk = RiskManager(RiskLimits(math.inf, math.inf, 0.0, math.inf))
        order = ParentOrder(
            symbol="WNBA | player_points | Aces",
            side=Side.BUY,
            quantity=25.0,
            limit_price=2.0,
            fair_price=1.9,
            strategy="SMART",
            source_signal="test",
            metadata={"edge": 0.2},
        )
        self.assertTrue(risk.check(order).accepted)


if __name__ == "__main__":
    unittest.main()
