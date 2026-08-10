import unittest
from unittest import mock

import performance_report


def _bet(**kw):
    base = {
        "id": 1,
        "market": "h2h",
        "selection": "Athletics",
        "odds": "+100",
        "odds_decimal": 2.0,
        "edge_pct": 2.5,
        "units": 1.0,
        "result": "WIN",
        "bet_source": "unified",
        "notes": "book=draftkings",
        "clv_edge_pct": 3.0,
        "closing_line_decimal": 1.94,
    }
    base.update(kw)
    return base


class PerformanceReportTest(unittest.TestCase):
    def _build(self, bets):
        with mock.patch.object(performance_report, "get_all_bets", return_value=bets):
            return performance_report.build_performance_report()

    def test_clv_coverage_is_reported_next_to_roi(self):
        # Half the bets have no close, so the headline CLV figure describes only
        # half the book — the report has to say so.
        bets = [
            _bet(id=1),
            _bet(id=2, clv_edge_pct=None, closing_line_decimal=None),
        ]
        report = self._build(bets)
        self.assertIn("ROI:", report)
        self.assertIn("CLV Tracked: 1/2 (50.0% coverage)", report)

    def test_per_market_coverage_exposes_untracked_markets(self):
        bets = [
            _bet(id=1, market="h2h"),
            _bet(id=2, market="totals_1st_5_innings", clv_edge_pct=None, closing_line_decimal=None),
            _bet(id=3, market="totals_1st_5_innings", clv_edge_pct=None, closing_line_decimal=None),
        ]
        report = self._build(bets)
        self.assertIn("By Market", report)
        market_line = next(line for line in report.splitlines() if "totals_1st_5_innings" in line)
        cov = market_line.split("|")[3].strip()
        self.assertEqual(cov, "0")

    def test_book_table_reports_average_clv(self):
        report = self._build([_bet(id=1), _bet(id=2, notes="book=fanduel")])
        book_header = next(line for line in report.splitlines() if line.startswith("| Book"))
        self.assertIn("AvgCLV", book_header)

    def test_no_bets_is_handled(self):
        self.assertIn("No bets found", self._build([]))


if __name__ == "__main__":
    unittest.main()
