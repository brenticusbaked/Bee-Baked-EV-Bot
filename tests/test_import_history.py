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
        mock_table.select.return_value.ilike.return_value.execute.return_value = MagicMock(
            data=[{"notes": "Historical import - ID: existing-1"}]
        )
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


if __name__ == "__main__":
    unittest.main()
