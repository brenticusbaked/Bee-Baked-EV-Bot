import unittest
from unittest.mock import patch

from execution_calibration import run_execution_calibration


class ExecutionCalibrationTests(unittest.TestCase):
    def test_calibration_logs_one_synthetic_order(self):
        with patch("execution_calibration.log_execution_report_to_db", return_value=True):
            result = run_execution_calibration()

        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["source_signal"], "synthetic_calibration")


if __name__ == "__main__":
    unittest.main()
