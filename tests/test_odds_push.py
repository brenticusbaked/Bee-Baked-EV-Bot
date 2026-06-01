import unittest
from unittest.mock import patch

from services.odds_push import build_connection_config, extract_cache_events, merge_push_message


class OddsPushTests(unittest.TestCase):
    def test_build_connection_config_supports_query_api_key(self):
        with patch.dict("os.environ", {"ODDS_PUSH_AUTH_MODE": "query"}, clear=False):
            url, headers = build_connection_config("wss://parlay-api.com/ws/odds/baseball_mlb", "secret_key")

        self.assertEqual(headers, {})
        self.assertEqual(url, "wss://parlay-api.com/ws/odds/baseball_mlb?apiKey=secret_key")

    def test_build_connection_config_defaults_to_header_api_key(self):
        url, headers = build_connection_config("wss://example.com/ws", "secret_key")

        self.assertEqual(url, "wss://example.com/ws")
        self.assertEqual(headers["X-API-Key"], "secret_key")

    def test_extracts_the_odds_api_shaped_event_from_wrapper(self):
        message = {
            "sport_key": "basketball_nba",
            "event": {
                "id": "evt_1",
                "home_team": "Miami Heat",
                "away_team": "Chicago Bulls",
                "bookmakers": [{"key": "draftkings", "markets": []}],
            },
        }

        events = extract_cache_events(message)

        self.assertEqual(events["basketball_nba"][0]["id"], "evt_1")

    def test_merge_push_message_updates_existing_book_market(self):
        cache = {
            "basketball_nba": [
                {
                    "id": "evt_1",
                    "bookmakers": [
                        {
                            "key": "draftkings",
                            "markets": [{"key": "h2h", "outcomes": [{"name": "Miami Heat", "price": 1.9}]}],
                        }
                    ],
                }
            ]
        }
        message = {
            "sport_key": "basketball_nba",
            "event": {
                "id": "evt_1",
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "markets": [{"key": "h2h", "outcomes": [{"name": "Miami Heat", "price": 2.0}]}],
                    }
                ],
            },
        }

        merged = merge_push_message(cache, message)

        self.assertEqual(merged, 1)
        outcome = cache["basketball_nba"][0]["bookmakers"][0]["markets"][0]["outcomes"][0]
        self.assertEqual(outcome["price"], 2.0)


if __name__ == "__main__":
    unittest.main()
