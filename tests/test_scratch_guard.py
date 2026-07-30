import unittest
from datetime import datetime, timedelta, timezone

from utils.scratch_guard import check_event_status


class ScratchGuardTests(unittest.TestCase):
    def test_future_aware_commence_time_is_valid(self):
        event = {
            "commence_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        }
        valid, reason = check_event_status(event)
        self.assertTrue(valid)
        self.assertEqual(reason, "ok")

    def test_future_naive_commence_time_is_valid(self):
        future_naive = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(tzinfo=None).isoformat()
        valid, reason = check_event_status({"commence_time": future_naive})
        self.assertTrue(valid)
        self.assertEqual(reason, "ok")

    def test_recently_started_game_is_within_grace_window(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        valid, reason = check_event_status({"commence_time": past})
        self.assertTrue(valid)
        self.assertEqual(reason, "ok")

    def test_old_started_game_is_rejected(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        valid, reason = check_event_status({"commence_time": past})
        self.assertFalse(valid)
        self.assertEqual(reason, "event already started")


if __name__ == "__main__":
    unittest.main()
