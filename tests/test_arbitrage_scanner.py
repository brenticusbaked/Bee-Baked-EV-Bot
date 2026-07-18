import unittest

from arbitrage_scanner import find_arbitrage_opportunities
from services.odds_reference import format_pinnacle_reference, format_pinnacle_spread_reference


def sample_event():
    return {
        "id": "evt_1",
        "home_team": "Aces",
        "away_team": "Liberty",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Aces", "price": 1.91},
                            {"name": "Liberty", "price": 1.91},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Aces", "point": -2.5, "price": 1.91},
                            {"name": "Liberty", "point": 2.5, "price": 1.91},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [{"key": "h2h", "outcomes": [{"name": "Aces", "price": 2.20}]}],
            },
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [{"key": "h2h", "outcomes": [{"name": "Liberty", "price": 2.20}]}],
            },
        ],
    }


class ArbitrageScannerTests(unittest.TestCase):
    def test_finds_two_way_arbitrage(self):
        cache = {"basketball_wnba": [sample_event()]}

        opportunities = find_arbitrage_opportunities(cache, min_profit=0.005)

        self.assertEqual(len(opportunities), 1)
        self.assertGreater(opportunities[0]["profit"], 0.09)
        self.assertEqual({leg["book"] for leg in opportunities[0]["outcomes"]}, {"DraftKings", "FanDuel"})

    def test_excludes_pinnacle_leg(self):
        # Best Liberty price is only at Pinnacle (not bettable). Without a second
        # American book on that side, no bettable arb should be reported.
        event = {
            "id": "evt_2",
            "home_team": "Aces",
            "away_team": "Liberty",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Liberty", "price": 2.50}]}],
                },
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Aces", "price": 2.20}]}],
                },
            ],
        }
        opportunities = find_arbitrage_opportunities({"basketball_wnba": [event]}, min_profit=0.005)
        self.assertEqual(opportunities, [])

    def test_requires_two_distinct_books(self):
        # A single American book pricing both sides is not a two-book arb.
        event = {
            "id": "evt_3",
            "home_team": "Aces",
            "away_team": "Liberty",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Aces", "price": 2.20},
                                {"name": "Liberty", "price": 2.20},
                            ],
                        }
                    ],
                }
            ],
        }
        opportunities = find_arbitrage_opportunities({"basketball_wnba": [event]}, min_profit=0.005)
        self.assertEqual(opportunities, [])

    def test_formats_pinnacle_reference(self):
        cache = {"basketball_wnba": [sample_event()]}

        reference = format_pinnacle_reference(cache, "basketball_wnba", "evt_1", "h2h", "Aces")

        self.assertEqual(reference, "Pinnacle -110")

    def test_formats_pinnacle_spread_reference_by_matchup(self):
        cache = {"basketball_wnba": [sample_event()]}

        reference = format_pinnacle_spread_reference(cache, "basketball_wnba", "Liberty @ Aces", "Aces")

        self.assertEqual(reference, "Pinnacle Aces -2.5 @ -110")


if __name__ == "__main__":
    unittest.main()
