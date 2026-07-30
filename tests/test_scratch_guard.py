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


if __name__ == "__main__":
    unittest.main()
