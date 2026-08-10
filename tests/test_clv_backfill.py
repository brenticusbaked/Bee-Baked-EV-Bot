import unittest
from unittest import mock

import clv_backfill


def _bet(**kw):
    base = {
        "id": 1,
        "sport": "baseball_mlb",
        "event_id": "evt1",
        "market": "h2h",
        "selection": "Athletics",
        "odds": "+100",
        "odds_decimal": 2.0,
    }
    base.update(kw)
    return base


class DetectTransposedTest(unittest.TestCase):
    """The repair must key off evidence, not a guess about magnitudes."""

    def test_transposed_row_is_detected(self):
        # Written by the buggy positional call: the decimal price landed in
        # clv_edge_pct and the percentage landed in closing_line_decimal.
        bet = _bet(closing_line_american="-111", clv_edge_pct=1.9, closing_line_decimal=5.2632)
        self.assertTrue(clv_backfill.detect_transposed(bet))

    def test_correct_row_is_left_alone(self):
        bet = _bet(closing_line_american="-111", clv_edge_pct=5.2632, closing_line_decimal=1.9)
        self.assertFalse(clv_backfill.detect_transposed(bet))

    def test_row_without_american_close_is_not_touched(self):
        bet = _bet(closing_line_american=None, clv_edge_pct=1.9, closing_line_decimal=5.26)
        self.assertFalse(clv_backfill.detect_transposed(bet))

    def test_row_missing_clv_is_not_transposed(self):
        bet = _bet(closing_line_american="-111", clv_edge_pct=None, closing_line_decimal=None)
        self.assertFalse(clv_backfill.detect_transposed(bet))


class RepairTest(unittest.TestCase):
    def _run(self, bets, apply=True):
        with mock.patch.object(clv_backfill, "get_all_bets", return_value=bets), \
             mock.patch.object(clv_backfill, "_fetch_historical_game_data", return_value=None), \
             mock.patch.object(clv_backfill, "update_bet_clv") as update:
            stats = clv_backfill.run_backfill(apply=apply)
        return stats, update

    def test_swap_restores_both_columns(self):
        bet = _bet(closing_line_american="-111", clv_edge_pct=1.9, closing_line_decimal=5.2632)
        stats, update = self._run([bet])
        self.assertEqual(stats["transposed"], 1)
        kwargs = update.call_args.kwargs
        self.assertAlmostEqual(kwargs["closing_line"], 1.9)
        self.assertAlmostEqual(kwargs["clv_pct"], 5.2632)

    def test_dry_run_writes_nothing(self):
        bet = _bet(closing_line_american="-111", clv_edge_pct=1.9, closing_line_decimal=5.2632)
        stats, update = self._run([bet], apply=False)
        self.assertEqual(stats["transposed"], 1)
        update.assert_not_called()


class BackfillMissingTest(unittest.TestCase):
    def test_missing_clv_is_filled_from_historical_odds(self):
        bet = _bet(clv_edge_pct=None, result="WIN", date="2024-01-01")
        history = {
            "bookmakers": [
                {"key": "pinnacle", "markets": [
                    {"key": "h2h", "outcomes": [{"name": "Athletics", "price": 1.9}]},
                ]},
            ]
        }
        with mock.patch.object(clv_backfill, "get_all_bets", return_value=[bet]), \
             mock.patch.object(clv_backfill, "_fetch_historical_game_data", return_value=history), \
             mock.patch.object(clv_backfill, "update_bet_clv") as update:
            stats = clv_backfill.run_backfill(apply=True)

        # Graded and long past the tracker's lookback window, so run_clv_tracker
        # would never revisit it.
        self.assertEqual(stats["backfilled"], 1)
        self.assertAlmostEqual(update.call_args.kwargs["clv_pct"], 5.2632, places=3)

    def test_event_absent_from_history_is_counted_not_invented(self):
        bet = _bet(clv_edge_pct=None)
        with mock.patch.object(clv_backfill, "get_all_bets", return_value=[bet]), \
             mock.patch.object(clv_backfill, "_fetch_historical_game_data", return_value=None), \
             mock.patch.object(clv_backfill, "update_bet_clv") as update:
            stats = clv_backfill.run_backfill(apply=True)

        self.assertEqual(stats["missing_no_history"], 1)
        self.assertEqual(stats["backfilled"], 0)
        update.assert_not_called()

    def test_already_tracked_bets_are_not_rewritten(self):
        bet = _bet(clv_edge_pct=3.5, closing_line_decimal=1.93, closing_line_american="-108")
        with mock.patch.object(clv_backfill, "get_all_bets", return_value=[bet]), \
             mock.patch.object(clv_backfill, "_fetch_historical_game_data", return_value=None), \
             mock.patch.object(clv_backfill, "update_bet_clv") as update:
            stats = clv_backfill.run_backfill(apply=True)

        self.assertEqual(stats["missing"], 0)
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
