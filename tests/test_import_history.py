import unittest
import sys
import types
from unittest.mock import MagicMock, patch

fake_pandas = types.SimpleNamespace(
    isna=lambda value: value is None,
    notna=lambda value: value is not None,
    read_csv=lambda *args, **kwargs: None,
)
sys.modules.setdefault("pandas", fake_pandas)

import import_history


class _FakeSeries(list):
    def isin(self, values):
        value_set = set(values)
        return [item in value_set for item in self]


class _FakeDataFrame:
    def __init__(self, rows):
        self._rows = list(rows)

    def __getitem__(self, key):
        if isinstance(key, str):
            return _FakeSeries([row.get(key) for row in self._rows])
        if isinstance(key, list):
            return _FakeDataFrame([row for row, keep in zip(self._rows, key) if keep])
        raise TypeError(key)

    def copy(self):
        return _FakeDataFrame(self._rows)

    def iterrows(self):
        for index, row in enumerate(self._rows):
            yield index, row


def _stub_history_pages(mock_table, rows):
    """Serve `rows` as the first page and an empty page after it."""
    pages = [rows, []]
    query = mock_table.select.return_value.ilike.return_value.order.return_value
    query.range.return_value.execute.side_effect = [MagicMock(data=page) for page in pages]
    return query


class TestImportHistory(unittest.TestCase):
    def test_skips_existing_bet_ids_and_inserts_missing_rows(self):
        df = _FakeDataFrame(
            [
                {
                    "bet_id": "existing-1",
                    "status": "SETTLED_WIN",
                    "amount": 3.0,
                    "odds": 2.0,
                    "ev": 0.05,
                    "sports": "Baseball",
                    "type": "straight",
                    "bet_info": "Existing bet",
                    "time_placed_iso": "2026-07-16T00:00:00Z",
                    "time_settled_iso": "2026-07-16T01:00:00Z",
                },
                {
                    "bet_id": "new-2",
                    "status": "SETTLED_LOSS",
                    "amount": 6.0,
                    "odds": 1.8,
                    "ev": -0.02,
                    "sports": "Baseball",
                    "type": "parlay",
                    "bet_info": "New bet",
                    "sportsbook": "Fanduel Sportsbook",
                    "time_placed_iso": "2026-07-16T02:00:00Z",
                    "time_settled_iso": "2026-07-16T03:00:00Z",
                },
                {
                    "bet_id": "pending-3",
                    "status": "PLACED",
                    "amount": 9.0,
                    "odds": 1.9,
                    "ev": 0.01,
                    "sports": "Baseball",
                    "type": "straight",
                    "bet_info": "Pending bet",
                    "time_placed_iso": "2026-07-16T04:00:00Z",
                    "time_settled_iso": "",
                },
            ]
        )

        mock_table = MagicMock()
        _stub_history_pages(mock_table, [{"notes": "Historical import - ID: existing-1"}])
        mock_table.insert.return_value.execute.return_value = MagicMock()

        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with (
            patch.object(import_history, "supabase", mock_supabase),
            patch.object(import_history.os.path, "exists", return_value=True),
            patch.object(import_history.pd, "read_csv", return_value=df),
            patch("builtins.print"),
        ):
            import_history.import_csv()

        mock_table.insert.assert_called_once()
        inserted_rows = mock_table.insert.call_args.args[0]
        self.assertEqual(len(inserted_rows), 1)
        self.assertEqual(
            inserted_rows[0]["notes"],
            "Historical import - ID: new-2;book=fanduel;book_key=fanduel",
        )
        self.assertEqual(inserted_rows[0]["result"], "LOSS")
        self.assertEqual(inserted_rows[0]["units"], 2.0)


class TestRepairHistory(unittest.TestCase):
    """The first repair run failed every chunk: a 3-column upsert is compiled to
    INSERT ... ON CONFLICT, so `date NOT NULL` rejected it before it could update.
    """

    def _run_repair(self, existing_rows, **kwargs):
        csv = _FakeDataFrame(
            [
                {
                    "bet_id": "bet-1",
                    "status": "SETTLED_WIN",
                    "sportsbook": "Fanduel Sportsbook",
                    "closing_line": None,
                }
            ]
        )
        mock_table = MagicMock()
        _stub_history_pages(mock_table, existing_rows)
        mock_supabase = MagicMock()
        mock_supabase.table.return_value = mock_table

        with (
            patch.object(import_history, "supabase", mock_supabase),
            patch.object(import_history.os.path, "exists", return_value=True),
            patch.object(import_history.pd, "read_csv", return_value=csv),
            patch("builtins.print"),
        ):
            import_history.repair_history(**kwargs)
        return mock_table

    def test_repair_writes_the_whole_row_back_not_a_three_column_patch(self):
        row = {
            "id": 7,
            "notes": "Historical import - ID: bet-1",
            "result": "win",
            "date": "2026-07-16T00:00:00Z",
            "units": 2.0,
        }
        mock_table = self._run_repair([row])

        payload = mock_table.upsert.call_args.args[0][0]
        self.assertEqual(payload["date"], row["date"])
        self.assertEqual(payload["units"], 2.0)
        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["result"], "WIN")
        self.assertEqual(
            payload["notes"],
            "Historical import - ID: bet-1;book=fanduel;book_key=fanduel",
        )

    def test_duplicate_rows_for_one_bet_are_deleted_keeping_the_earliest(self):
        rows = [
            {"id": 1, "notes": "Historical import - ID: bet-1", "result": "win", "date": "d"},
            {"id": 2, "notes": "Historical import - ID: bet-1", "result": "win", "date": "d"},
            {"id": 3, "notes": "Historical import - ID: bet-1", "result": "win", "date": "d"},
        ]
        mock_table = self._run_repair(rows)

        self.assertEqual([p["id"] for p in mock_table.upsert.call_args.args[0]], [1])
        mock_table.delete.return_value.in_.assert_called_once_with("id", [2, 3])

    def test_dedupe_can_be_declined(self):
        rows = [
            {"id": 1, "notes": "Historical import - ID: bet-1", "result": "win", "date": "d"},
            {"id": 2, "notes": "Historical import - ID: bet-1", "result": "win", "date": "d"},
        ]
        mock_table = self._run_repair(rows, dedupe=False)

        mock_table.delete.assert_not_called()

    def test_dry_run_writes_nothing(self):
        rows = [
            {"id": 1, "notes": "Historical import - ID: bet-1", "result": "win", "date": "d"},
            {"id": 2, "notes": "Historical import - ID: bet-1", "result": "win", "date": "d"},
        ]
        mock_table = self._run_repair(rows, dry_run=True)

        mock_table.upsert.assert_not_called()
        mock_table.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
