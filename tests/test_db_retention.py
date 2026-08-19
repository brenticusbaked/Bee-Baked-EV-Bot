"""Retention pruning: window resolution, per-table age columns, dry run. No network."""

import unittest
from unittest.mock import MagicMock, patch

import db_manager
from services import db_retention


class _Table:
    """Records the filter chain the pruner builds against one table."""

    def __init__(self, recorder, name, count):
        self.recorder = recorder
        self.name = name
        self._count = count
        self.deleted = False

    def select(self, *_args, **_kwargs):
        return self

    def delete(self):
        self.deleted = True
        self.recorder.setdefault("deletes", []).append(self.name)
        return self

    def lt(self, column, cutoff):
        self.recorder.setdefault("filters", []).append((self.name, column, cutoff, self.deleted))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return MagicMock(count=self._count, data=[])


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.recorder = {}
        self.counts = {}
        client = MagicMock()
        client.table.side_effect = lambda name: _Table(self.recorder, name, self.counts.get(name, 0))
        patcher = patch.object(db_manager, "supabase", client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_table_is_aged_on_its_own_timestamp_column(self):
        self.counts = {name: 5 for name in db_retention.AGE_COLUMN}
        db_retention.prune_stale_rows()
        used = {(table, column) for table, column, _cutoff, deleting in self.recorder["filters"] if deleting}
        for table, column in db_retention.AGE_COLUMN.items():
            self.assertIn((table, column), used)
        self.assertEqual(set(db_retention.AGE_COLUMN), set(db_retention.DEFAULT_RETENTION_DAYS))

    def test_player_logs_age_on_game_date(self):
        self.counts = {"mlb_player_logs": 3}
        db_retention.prune_stale_rows()
        self.assertIn(
            ("mlb_player_logs", "game_date"),
            {(table, column) for table, column, _c, deleting in self.recorder["filters"] if deleting},
        )

    def test_dry_run_counts_without_deleting(self):
        self.counts = {"historical_odds": 12}
        with patch.dict("os.environ", {"DB_RETENTION_DRY_RUN": "true"}, clear=False):
            result = db_retention.prune_stale_rows()
        self.assertEqual(self.recorder.get("deletes", []), [])
        self.assertEqual(result["count"], 0)
        self.assertIn("dry run", result["detail"])

    def test_tables_with_nothing_stale_are_not_deleted_from(self):
        self.counts = {}
        result = db_retention.prune_stale_rows()
        self.assertEqual(self.recorder.get("deletes", []), [])
        self.assertEqual(result["count"], 0)

    def test_bet_and_execution_history_is_never_pruned(self):
        # The ROI/CLV record is the point of the database; only market data ages out.
        protected = {"bets_log", "bet_history", "execution_orders", "execution_fills", "syndicate_bets"}
        self.assertFalse(protected & set(db_retention.AGE_COLUMN))
        self.assertFalse(protected & set(db_retention.PLAYER_LOG_TABLES))

    def test_window_overrides_come_from_the_environment(self):
        with patch.dict("os.environ", {"RETENTION_DAYS_HISTORICAL_ODDS": "10"}, clear=False):
            self.assertEqual(db_retention.retention_days("historical_odds", 45), 10)
        with patch.dict("os.environ", {"RETENTION_DAYS_HISTORICAL_ODDS": "0"}, clear=False):
            self.assertEqual(db_retention.retention_days("historical_odds", 45), 45)
        with patch.dict("os.environ", {"RETENTION_DAYS_HISTORICAL_ODDS": "soon"}, clear=False):
            self.assertEqual(db_retention.retention_days("historical_odds", 45), 45)

    def test_odds_window_outlives_the_clv_and_grading_lookbacks(self):
        self.assertGreaterEqual(db_retention.DEFAULT_RETENTION_DAYS["historical_odds"], 30)

    def test_no_supabase_client_is_a_skip_not_a_crash(self):
        with patch.object(db_manager, "supabase", None):
            result = db_retention.prune_stale_rows()
        self.assertIn("skipped", result["detail"])


class IngestBlobTests(unittest.TestCase):
    """The write-only JSON copies were most of the disk; keep them gone."""

    def test_ingesters_no_longer_write_the_duplicate_json_blobs(self):
        for path in ("sgo_sharp_ingest.py", "supabase/functions/odds-cache-ingest/index.ts"):
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("raw_outcome:", source, path)
            self.assertNotIn('"raw_outcome":', source, path)
            self.assertNotIn("raw_event: event", source, path)


if __name__ == "__main__":
    unittest.main()
