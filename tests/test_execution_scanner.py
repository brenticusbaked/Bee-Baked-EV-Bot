import unittest
from unittest import mock

import execution_scanner
from execution_scanner import (
    _execution_desk_alert_description,
    _send_execution_desk_alerts,
    _synthetic_calibration_report,
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

    def test_alerts_skip_calibration_and_respect_toggle(self):
        with mock.patch.object(execution_scanner, "ENABLE_EXECUTION_DESK_ALERTS", True), \
             mock.patch.object(execution_scanner, "EXECUTION_DESK_WEBHOOK_URL", "https://hook"), \
             mock.patch.object(execution_scanner, "send_discord_alert", return_value=True) as send:
            sent = _send_execution_desk_alerts([_candidate(), _candidate(calibration=True)])
        self.assertEqual(sent, 1)
        self.assertEqual(send.call_count, 1)

    def test_alerts_disabled_sends_nothing(self):
        with mock.patch.object(execution_scanner, "ENABLE_EXECUTION_DESK_ALERTS", False), \
             mock.patch.object(execution_scanner, "send_discord_alert") as send:
            sent = _send_execution_desk_alerts([_candidate()])
        self.assertEqual(sent, 0)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
