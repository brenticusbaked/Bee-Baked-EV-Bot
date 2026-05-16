import unittest

from execution_scanner import _synthetic_calibration_report


class ExecutionScannerTests(unittest.TestCase):
    def test_synthetic_calibration_report_is_tagged_and_filled(self):
        report = _synthetic_calibration_report()

        self.assertEqual(report["status"], "FILLED")
        self.assertEqual(report["parent_order"]["source_signal"], "synthetic_calibration")
        self.assertTrue(report["parent_order"]["metadata"]["calibration"])
        self.assertTrue(report["parent_order"]["metadata"]["synthetic"])
        self.assertEqual(report["child_orders"][0]["venue_id"], "paper_healthcheck")


if __name__ == "__main__":
    unittest.main()
