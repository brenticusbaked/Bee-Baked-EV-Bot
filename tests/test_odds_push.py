import unittest

from services.odds_push import extract_cache_events, merge_push_message


class OddsPushTests(unittest.TestCase):
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
