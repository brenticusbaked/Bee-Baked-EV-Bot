import unittest
import sys
import types
from unittest.mock import patch


class NewsParserTests(unittest.TestCase):
    def test_news_page_text_fallback_extracts_injury_updates(self):
        fake_alerts = types.ModuleType("services.alerts")
        fake_alerts.send_discord_alert = lambda *args, **kwargs: True
        fake_http_client = types.ModuleType("services.http_client")
        fake_http_client.request = lambda *args, **kwargs: None
        chunks = [
            "News",
            "Skylar Diggins",
            "Won't return versus Phoenix",
            "G Chicago Sky",
            "May 15, 2026",
            "Diggins won't return to Friday's game against Phoenix due to an eye injury.",
        ]

        with patch.dict(sys.modules, {"services.alerts": fake_alerts, "services.http_client": fake_http_client}):
            from scraper_bot import _alerts_from_news_page_text

            alerts = _alerts_from_news_page_text("WNBA", chunks)

        self.assertEqual(len(alerts), 1)
        self.assertIn("Skylar Diggins", alerts[0]["title"])
        self.assertIn("eye injury", alerts[0]["desc"])


if __name__ == "__main__":
    unittest.main()
