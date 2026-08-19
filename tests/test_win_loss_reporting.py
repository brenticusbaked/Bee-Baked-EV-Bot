"""Win/loss reporting across the two result vocabularies in bets_log.

The historical CSV import wrote lower-case results and no sportsbook, so the
overall record read empty and the per-book breakdown had nothing to group on.
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

import daily_slips_report
import import_history
import performance_report
from services import book_weights
from utils.results import LOSS, PUSH, WIN, book_from_notes, is_graded, normalize_result


class NormalizeResultTests(unittest.TestCase):
    def test_grader_and_import_vocabularies_both_normalize(self):
        for raw in ("WIN", "win", "Won", " w "):
            self.assertEqual(normalize_result(raw), WIN, raw)
        for raw in ("LOSS", "loss", "Lost", "l"):
            self.assertEqual(normalize_result(raw), LOSS, raw)
        for raw in ("PUSH", "push", "void", "SETTLED_VOID", "refund"):
            self.assertEqual(normalize_result(raw), PUSH, raw)

    def test_ungraded_values_are_not_results(self):
        for raw in (None, "", "   ", "pending", "PLACED", float("nan")):
            self.assertEqual(normalize_result(raw), "")
            self.assertFalse(is_graded(raw))

    def test_book_is_read_from_either_notes_key(self):
        self.assertEqual(book_from_notes("book=DraftKings;market=h2h"), "draftkings")
        self.assertEqual(book_from_notes("model=x;book_key=fanduel"), "fanduel")
        self.assertEqual(book_from_notes("Historical import - ID: abc;book=betmgm"), "betmgm")
        self.assertEqual(book_from_notes("Fanduel Sportsbook"), "unknown")
        self.assertEqual(book_from_notes(None), "unknown")

    def test_display_names_and_keys_collapse_to_one_book(self):
        self.assertEqual(
            book_from_notes("book=Fanduel Sportsbook"),
            book_from_notes("book=fanduel"),
        )


def _bet(**kw):
    base = {
        "id": 1,
        "market": "h2h",
        "odds": "+100",
        "odds_decimal": 2.0,
        "edge_pct": 2.5,
        "units": 1.0,
        "result": "WIN",
        "bet_source": "unified",
        "notes": "book=draftkings",
    }
    base.update(kw)
    return base


class OverallRecordTests(unittest.TestCase):
    def _build(self, bets):
        with mock.patch.object(performance_report, "get_all_bets", return_value=bets):
            return performance_report.build_performance_report()

    def test_lower_case_imported_results_count_toward_the_record(self):
        bets = [
            _bet(id=1, result="win"),
            _bet(id=2, result="win"),
            _bet(id=3, result="loss"),
            _bet(id=4, result="push"),
        ]
        report = self._build(bets)
        self.assertIn("Record: 2W-1L", report)
        self.assertIn("Graded: 4", report)

    def test_ungraded_bets_stay_out_of_the_record(self):
        report = self._build([_bet(id=1, result="win"), _bet(id=2, result=None)])
        self.assertIn("Record: 1W-0L", report)
        self.assertIn("Graded: 1", report)

    def test_a_graded_bet_without_an_edge_still_counts(self):
        # Parlays came out of the export with no EV; they are still wins and losses.
        bets = [
            _bet(id=1, result="win", edge_pct=2.5),
            _bet(id=2, result="loss", edge_pct=None, edge=None),
        ]
        report = self._build(bets)
        self.assertIn("Record: 1W-1L", report)
        self.assertIn("EV-tagged: 1", report)

    def test_imported_book_names_group_with_scanner_book_keys(self):
        bets = [
            _bet(id=1, result="win", notes="book=draftkings;market=h2h"),
            _bet(id=2, result="loss", notes="Historical import - ID: x;book=draftkings"),
        ]
        report = self._build(bets)
        book_lines = [line for line in report.splitlines() if line.startswith("| draftkings")]
        self.assertEqual(len(book_lines), 1)
        self.assertIn("| 2 ", book_lines[0])


class DailyByBookTests(unittest.TestCase):
    def _build(self, bets, now=datetime(2026, 7, 30, tzinfo=timezone.utc)):
        with mock.patch.object(daily_slips_report, "get_all_bets", return_value=bets), \
             mock.patch.object(daily_slips_report, "get_local_now", return_value=now):
            return daily_slips_report.build_daily_slips_report()

    def _settled(self, **kw):
        base = {
            "date": "2026-07-29",
            "graded_at": "2026-07-29T18:00:00Z",
            "result": "WIN",
            "odds": "+100",
            "units": 1.0,
            "notes": "book=draftkings",
        }
        base.update(kw)
        return base

    def test_daily_record_is_broken_out_by_book(self):
        bets = [
            self._settled(id=1, result="win", notes="book=draftkings"),
            self._settled(id=2, result="loss", notes="book=draftkings"),
            self._settled(id=3, result="win", notes="book=Fanduel Sportsbook"),
        ]
        report = self._build(bets)
        self.assertIn("Settled Record: 2-1", report)
        self.assertIn("By Book:", report)
        book_line = next(line for line in report.splitlines() if line.startswith("By Book:"))
        self.assertIn("`draftkings` 1-1", book_line)
        self.assertIn("`fanduel` 1-0", book_line)

    def test_lower_case_results_reach_the_daily_and_lifetime_records(self):
        bets = [
            self._settled(id=1, result="win"),
            self._settled(id=2, result="loss"),
            # Older bet, lifetime only.
            self._settled(id=3, result="win", date="2026-07-01", graded_at="2026-07-01T18:00:00Z"),
        ]
        report = self._build(bets)
        self.assertIn("Settled Record: 1-1", report)
        self.assertIn("Lifetime Record: 2-1", report)
        self.assertIn("Lifetime By Book:", report)

    def test_pushes_are_reported_without_being_counted_as_losses(self):
        bets = [self._settled(id=1, result="win"), self._settled(id=2, result="void")]
        report = self._build(bets)
        self.assertIn("Settled Record: 1-0-1", report)

    def test_bets_missing_a_book_group_as_unknown(self):
        report = self._build([self._settled(id=1, result="win", notes="model=mlb_f5")])
        self.assertIn("`unknown` 1-0", report)

    def test_status_summary_carries_the_record_and_books(self):
        bets = [self._settled(id=1, result="win"), self._settled(id=2, result="loss")]
        with mock.patch.object(daily_slips_report, "get_all_bets", return_value=bets):
            summary = daily_slips_report.build_overall_status_summary()
        self.assertIn("Record: 1-1", summary)
        self.assertIn("`draftkings` 1-1", summary)


class BookWeightTests(unittest.TestCase):
    def test_imported_history_now_feeds_book_weights(self):
        bets = [
            {
                "notes": f"Historical import - ID: {i};book=fanduel",
                "result": "win" if i % 2 else "loss",
                "clv_edge_pct": 1.0,
            }
            for i in range(10)
        ]
        with mock.patch.object(book_weights, "get_all_bets", return_value=bets), \
             mock.patch.object(book_weights, "history_book_factors", return_value={}):
            weights = book_weights.get_book_weights(min_sample=5)
        self.assertIn("fanduel", weights)


class ImporterTests(unittest.TestCase):
    def test_results_are_written_in_the_canonical_vocabulary(self):
        self.assertEqual(set(import_history._STATUS_MAP.values()), {WIN, LOSS, PUSH})

    def test_notes_carry_the_normalized_book(self):
        notes = import_history._historical_notes("us-ky:1", "Fanduel Sportsbook")
        self.assertEqual(book_from_notes(notes), "fanduel")
        match = import_history._HISTORICAL_NOTE_RE.match(notes)
        self.assertEqual(match.group("bet_id"), "us-ky:1")

    def test_ev_fractions_are_stored_as_percentages(self):
        self.assertAlmostEqual(import_history._edge_pct(0.0198), 1.98)
        self.assertAlmostEqual(import_history._edge_pct(2.5), 2.5)
        self.assertEqual(import_history._edge_pct(None), 0.0)


if __name__ == "__main__":
    unittest.main()
