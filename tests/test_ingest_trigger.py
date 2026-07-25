import unittest
from unittest import mock

from services import ingest_trigger


class FunctionUrlTests(unittest.TestCase):
    def test_prefers_explicit_url(self):
        with mock.patch.dict("os.environ", {"ODDS_INGEST_FUNCTION_URL": "https://x/fn"}, clear=True):
            self.assertEqual(ingest_trigger._function_url(), "https://x/fn")

    def test_derives_from_supabase_url(self):
        with mock.patch.dict("os.environ", {"SUPABASE_URL": "https://ref.supabase.co/"}, clear=True):
            self.assertEqual(
                ingest_trigger._function_url(),
                "https://ref.supabase.co/functions/v1/odds-cache-ingest",
            )

    def test_empty_when_unconfigured(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(ingest_trigger._function_url(), "")


class TriggerOddsIngestTests(unittest.TestCase):
    def test_skips_without_url(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            result = ingest_trigger.trigger_odds_ingest()
        self.assertEqual(result["count"], 0)
        self.assertIn("skipped", result["detail"])

    def test_posts_force_and_summarizes(self):
        fake = mock.Mock()
        fake.status_code = 200
        fake.json.return_value = {"status": "ok", "oddsRows": 42, "fixtures": 7, "remaining": 19000}
        env = {"SUPABASE_URL": "https://ref.supabase.co", "ODDS_INGEST_FUNCTION_SECRET": "sek"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch.object(ingest_trigger.requests, "post", return_value=fake) as post:
            result = ingest_trigger.trigger_odds_ingest()

        self.assertEqual(result["count"], 42)
        self.assertIn("42 odds rows", result["detail"])
        _args, kwargs = post.call_args
        self.assertEqual(kwargs["json"], {"trigger": "manual_pipeline", "force": True})
        self.assertEqual(kwargs["headers"]["x-ingest-secret"], "sek")

    def test_network_error_is_non_fatal(self):
        env = {"SUPABASE_URL": "https://ref.supabase.co"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch.object(ingest_trigger.requests, "post", side_effect=RuntimeError("boom")):
            result = ingest_trigger.trigger_odds_ingest()
        self.assertEqual(result["count"], 0)
        self.assertIn("kept previous cache", result["detail"])


if __name__ == "__main__":
    unittest.main()
