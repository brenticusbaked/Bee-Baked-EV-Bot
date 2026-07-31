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

    def test_started_status_does_not_override_future_commence_time(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        valid, reason = check_event_status({"commence_time": future, "status": "started"})
        self.assertTrue(valid)
        self.assertEqual(reason, "ok")

    def test_recently_started_game_is_within_grace_window(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        valid, reason = check_event_status({"commence_time": past})
        self.assertTrue(valid)
        self.assertEqual(reason, "ok")

    def test_old_started_game_is_rejected(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        valid, reason = check_event_status({"commence_time": past, "status": "started"})
        self.assertFalse(valid)
        self.assertEqual(reason, "event started")

    def test_same_day_scheduled_game_is_allowed_within_grace(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        valid, reason = check_event_status({"commence_time": past, "status": "scheduled"})
        self.assertTrue(valid)
        self.assertEqual(reason, "ok")

    def test_yesterday_scheduled_game_is_rejected(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1, hours=1)).isoformat()
        valid, reason = check_event_status({"commence_time": past, "status": "scheduled"})
        self.assertFalse(valid)
        self.assertEqual(reason, "event already started")


if __name__ == "__main__":
    unittest.main()
