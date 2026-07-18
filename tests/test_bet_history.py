import unittest

from bet_history import normalize_transaction, summarize
from utils.book_names import normalize_book


def _row(**overrides):
    base = {
        "bet_id": "b1",
        "sportsbook": "Fanduel Sportsbook",
        "type": "straight",
        "status": "SETTLED_WIN",
        "odds": "2.0",
        "closing_line": "1.9",
        "ev": "0.03",
        "amount": "10",
        "profit": "10",
        "time_placed_iso": "2026-07-16T23:31:46.312Z",
        "time_settled_iso": "2026-07-17T17:48:47.965Z",
        "sports": "Baseball",
        "leagues": "MLB",
        "tags": "SS ",
    }
    base.update(overrides)
    return base


class TestNormalizeBook(unittest.TestCase):
    def test_variants_map_to_canonical(self):
        self.assertEqual(normalize_book("Fanduel Sportsbook"), "fanduel")
        self.assertEqual(normalize_book("FanDuel"), "fanduel")
        self.assertEqual(normalize_book("Draftkings Sportsbook"), "draftkings")
        self.assertEqual(normalize_book("theScore Bet"), "thescore")
        self.assertEqual(normalize_book("ESPN BET"), "thescore")
        self.assertEqual(normalize_book("Novig"), "novig")

    def test_unknown_slugifies(self):
        self.assertEqual(normalize_book("Some New Book"), "some_new_book")
        self.assertEqual(normalize_book(""), "unknown")


class TestNormalizeTransaction(unittest.TestCase):
    def test_maps_status_and_types(self):
        record = normalize_transaction(_row())
        self.assertEqual(record["book"], "fanduel")
        self.assertEqual(record["result"], "won")
        self.assertEqual(record["odds_decimal"], 2.0)
        self.assertEqual(record["tags"], ["SS"])

    def test_pending_and_void(self):
        self.assertEqual(normalize_transaction(_row(status="PLACED"))["result"], "pending")
        self.assertEqual(normalize_transaction(_row(status="SETTLED_VOID"))["result"], "void")

    def test_blank_row_returns_none(self):
        self.assertIsNone(normalize_transaction({"sportsbook": "", "status": ""}))


class TestSummarize(unittest.TestCase):
    def setUp(self):
        self.records = [
            normalize_transaction(_row(bet_id="w", status="SETTLED_WIN", amount="10", profit="10", ev="0.06", odds="2.0", closing_line="1.8")),
            normalize_transaction(_row(bet_id="l", status="SETTLED_LOSS", amount="10", profit="-10", ev="-0.02", odds="1.9", closing_line="2.0")),
            normalize_transaction(_row(bet_id="p", status="PLACED", amount="10", profit="0", ev="0.10")),
            normalize_transaction(_row(bet_id="v", status="SETTLED_VOID", amount="10", profit="0", ev="0.03")),
        ]

    def test_excludes_pending_and_void_from_roi(self):
        summary = summarize(self.records)
        self.assertEqual(summary["overall"]["n"], 2)
        self.assertEqual(summary["overall"]["stake"], 20.0)
        self.assertEqual(summary["overall"]["profit"], 0.0)

    def test_ev_bucket_assignment(self):
        summary = summarize(self.records)
        self.assertIn("5-10%", summary["ev_buckets"])
        self.assertIn("neg", summary["ev_buckets"])
        self.assertEqual(summary["ev_buckets"]["5-10%"]["roi"], 1.0)
        self.assertEqual(summary["ev_buckets"]["neg"]["roi"], -1.0)

    def test_clv_baseline(self):
        summary = summarize(self.records)
        clv = summary["clv_by_book"]["fanduel"]
        # win bet beat close (2.0 vs 1.8), loss bet did not (1.9 vs 2.0)
        self.assertEqual(clv["n"], 2)
        self.assertEqual(clv["pct_beat_close"], 0.5)


if __name__ == "__main__":
    unittest.main()
