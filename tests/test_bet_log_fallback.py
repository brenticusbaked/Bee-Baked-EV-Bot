import unittest
from unittest.mock import patch

import db_manager


class _FakeExecute:
    data = []


class _FakeInsert:
    def __init__(self, table, payload):
        self.table = table
        self.payload = payload

    def execute(self):
        self.table.payloads.append(self.payload)
        if len(self.table.payloads) == 1:
            raise Exception("column edge_pct does not exist")
        return _FakeExecute()


class _FakeTable:
    def __init__(self):
        self.payloads = []

    def insert(self, payload):
        return _FakeInsert(self, payload)


class _FakeSupabase:
    def __init__(self):
        self.bets_log = _FakeTable()

    def table(self, name):
        self.name = name
        return self.bets_log


class BetLogFallbackTests(unittest.TestCase):
    def test_log_bet_retries_legacy_payload_when_full_insert_fails(self):
        fake = _FakeSupabase()
        with patch.object(db_manager, "supabase", fake):
            ok = db_manager.log_bet_to_db(
                matchup="A @ B",
                market="spreads",
                selection="A -1.5",
                odds="+100",
                edge_val=0.03,
                units="1.0",
                fair_price="-110",
                sport="basketball_nba",
                event_id="event-1",
            )

        self.assertTrue(ok)
        self.assertEqual(len(fake.bets_log.payloads), 2)
        self.assertIn("edge_pct", fake.bets_log.payloads[0])
        self.assertNotIn("edge_pct", fake.bets_log.payloads[1])


if __name__ == "__main__":
    unittest.main()
