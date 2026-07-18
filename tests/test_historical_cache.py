import unittest

from db_manager import assemble_cache


class AssembleCacheTests(unittest.TestCase):
    def _fixtures(self):
        return [
            {
                "id": "evt_1",
                "sport_key": "basketball_nba",
                "commence_time": "2026-01-01T00:00:00Z",
                "home_team": "Lakers",
                "away_team": "Celtics",
            }
        ]

    def test_reconstructs_nested_cache(self):
        odds_rows = [
            {
                "fixture_id": "evt_1",
                "sport_key": "basketball_nba",
                "bookmaker_key": "pinnacle",
                "bookmaker_title": "Pinnacle",
                "market_key": "h2h",
                "outcome_name": "Lakers",
                "outcome_description": None,
                "point": None,
                "price_decimal": 1.91,
                "last_update": "2026-01-01T00:00:00Z",
                "captured_at": "2026-01-01T00:00:00Z",
            },
            {
                "fixture_id": "evt_1",
                "sport_key": "basketball_nba",
                "bookmaker_key": "pinnacle",
                "bookmaker_title": "Pinnacle",
                "market_key": "h2h",
                "outcome_name": "Celtics",
                "outcome_description": None,
                "point": None,
                "price_decimal": 1.95,
                "last_update": "2026-01-01T00:00:00Z",
                "captured_at": "2026-01-01T00:00:00Z",
            },
        ]

        cache = assemble_cache(self._fixtures(), odds_rows)

        self.assertIn("basketball_nba", cache)
        events = cache["basketball_nba"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["home_team"], "Lakers")
        self.assertEqual(len(event["bookmakers"]), 1)
        market = event["bookmakers"][0]["markets"][0]
        self.assertEqual(market["key"], "h2h")
        self.assertEqual(len(market["outcomes"]), 2)
        # Internal assembly scratch keys must be stripped.
        self.assertNotIn("_books", event)
        self.assertNotIn("_markets", event["bookmakers"][0])

    def test_freshest_price_wins(self):
        odds_rows = [
            {
                "fixture_id": "evt_1",
                "sport_key": "basketball_nba",
                "bookmaker_key": "pinnacle",
                "market_key": "h2h",
                "outcome_name": "Lakers",
                "point": None,
                "price_decimal": 1.80,
                "last_update": "2026-01-01T00:00:00Z",
                "captured_at": "2026-01-01T00:00:00Z",
            },
            {
                "fixture_id": "evt_1",
                "sport_key": "basketball_nba",
                "bookmaker_key": "pinnacle",
                "market_key": "h2h",
                "outcome_name": "Lakers",
                "point": None,
                "price_decimal": 2.05,
                "last_update": "2026-01-01T00:05:00Z",
                "captured_at": "2026-01-01T00:05:00Z",
            },
        ]

        cache = assemble_cache(self._fixtures(), odds_rows)
        outcomes = cache["basketball_nba"][0]["bookmakers"][0]["markets"][0]["outcomes"]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["price"], 2.05)

    def test_player_prop_point_and_description_preserved(self):
        odds_rows = [
            {
                "fixture_id": "evt_1",
                "sport_key": "basketball_nba",
                "bookmaker_key": "pinnacle",
                "market_key": "player_points",
                "outcome_name": "Over",
                "outcome_description": "LeBron James",
                "point": 25.5,
                "price_decimal": 1.90,
                "last_update": "2026-01-01T00:00:00Z",
                "captured_at": "2026-01-01T00:00:00Z",
            },
        ]

        cache = assemble_cache(self._fixtures(), odds_rows)
        outcome = cache["basketball_nba"][0]["bookmakers"][0]["markets"][0]["outcomes"][0]
        self.assertEqual(outcome["description"], "LeBron James")
        self.assertEqual(outcome["point"], 25.5)


if __name__ == "__main__":
    unittest.main()
