import unittest
from datetime import datetime, timezone
from unittest import mock

import daily_slips_report


class DailySlipsReportTests(unittest.TestCase):
    def test_report_includes_roi_and_net_units(self):
        report_date = "2026-07-29"
        bets = [
            {
                "date": report_date,
                "graded_at": "2026-07-29T12:00:00Z",
                "clv_tracked_at": "2026-07-29T12:05:00Z",
                "result": "WIN",
                "odds": "+150",
                "units": "1",
                "bet_source": "unified_bot",
            },
            {
                "date": report_date,
                "graded_at": "2026-07-29T15:00:00Z",
                "clv_tracked_at": "2026-07-29T15:05:00Z",
                "result": "LOSS",
                "odds": "-100",
                "units": "1",
                "bet_source": "unified_bot",
            },
        ]

        with mock.patch.object(daily_slips_report, "get_all_bets", return_value=bets), \
             mock.patch.object(
                 daily_slips_report,
                 "get_local_now",
                 return_value=datetime(2026, 7, 30, tzinfo=timezone.utc),
             ):
            report = daily_slips_report.build_daily_slips_report()

        self.assertIn("Settled Record: 1-1", report)
        self.assertIn("Win%:", report)
        self.assertIn("ROI: +25.0%", report)
        self.assertIn("Net Units: +0.50", report)


if __name__ == "__main__":
    unittest.main()
