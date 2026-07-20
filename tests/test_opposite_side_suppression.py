import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import unified_bot


def _future_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")


def _wnba_h2h_cache(home_soft_price: float, away_soft_price: float) -> dict:
    """WNBA event whose soft moneyline prices are the only tunable knob."""
    return {
        "basketball_wnba": [
            {
                "id": "evt_wnba",
                "home_team": "Aces",
                "away_team": "Liberty",
                "commence_time": _future_iso(),
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "title": "Pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Aces", "price": 1.90},
                                    {"name": "Liberty", "price": 1.90},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "draftkings",
                        "title": "DraftKings",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Aces", "price": home_soft_price},
                                    {"name": "Liberty", "price": away_soft_price},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
    }


def _both_sides_prop_event() -> dict:
    """Symmetric prop where a soft book beats the fair line on BOTH sides."""
    return {
        "id": "evt_prop_both",
        "home_team": "Lakers",
        "away_team": "Celtics",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {"name": "Over", "description": "LeBron James", "point": 25.5, "price": 1.90},
                            {"name": "Under", "description": "LeBron James", "point": 25.5, "price": 1.90},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {"name": "Over", "description": "LeBron James", "point": 25.5, "price": 2.10},
                            {"name": "Under", "description": "LeBron James", "point": 25.5, "price": 2.10},
                        ],
                    }
                ],
            },
        ],
    }


class SideTokenTests(unittest.TestCase):
    def test_market_side_token_over_under(self):
        self.assertEqual(unified_bot._market_side_token("Over"), "over")
        self.assertEqual(unified_bot._market_side_token("Under 8.5"), "under")

    def test_market_side_token_team_drops_point(self):
        self.assertEqual(unified_bot._market_side_token("Boston Celtics"), "boston celtics")

    def test_side_token_from_selection_strips_line(self):
        self.assertEqual(unified_bot._side_token_from_selection("Boston Celtics -3.5"), "boston celtics")
        self.assertEqual(unified_bot._side_token_from_selection("Over 8.5"), "over")
        self.assertEqual(unified_bot._side_token_from_selection("Aces"), "aces")
        self.assertEqual(unified_bot._side_token_from_selection("Los Angeles Lakers +120"), "los angeles lakers")

    def test_build_logged_sides_ignores_graded_and_groups(self):
        rows = [
            {"event_id": "e1", "market": "h2h", "selection": "Aces", "result": None},
            {"event_id": "e1", "market": "totals", "selection": "Over 8.5", "result": None},
            {"event_id": "e1", "market": "h2h", "selection": "Liberty", "result": "win"},
        ]
        sides = unified_bot._build_logged_sides(rows)
        self.assertEqual(sides[("e1", "H2H")], {"aces"})  # graded 'Liberty' excluded
        self.assertEqual(sides[("e1", "TOTALS")], {"over"})


class MainMarketOppositeSuppressionTests(unittest.TestCase):
    def _run(self, cache, today_bets):
        patches = {
            "get_book_weights": {},
            "get_all_graded_bets": [],
            "get_today_bets": today_bets,
            "validated_ev_floor": None,
        }
        with patch.object(unified_bot, "get_book_weights", return_value=patches["get_book_weights"]), \
                patch.object(unified_bot, "get_all_graded_bets", return_value=[]), \
                patch.object(unified_bot, "get_today_bets", return_value=today_bets), \
                patch.object(unified_bot, "validated_ev_floor", return_value=None), \
                patch.object(unified_bot, "is_already_logged", return_value=False), \
                patch.object(unified_bot, "log_bet_to_db", return_value=True), \
                patch.object(unified_bot, "send_discord_alert", return_value=None):
            return unified_bot.scan_markets(cache_override=cache)

    def test_both_moneylines_ev_emits_one_side(self):
        # Both soft moneylines beat the fair 50/50 line -> only the top side alerts.
        result = self._run(_wnba_h2h_cache(2.10, 2.10), today_bets=[])
        self.assertEqual(result["count"], 1)

    def test_opposite_of_already_logged_side_is_suppressed(self):
        # Only the away side is +EV, but the home side was already alerted today.
        cache = _wnba_h2h_cache(home_soft_price=1.50, away_soft_price=2.10)
        logged = [{"event_id": "evt_wnba", "market": "h2h", "selection": "Aces", "result": None}]
        self.assertEqual(self._run(cache, today_bets=logged)["count"], 0)
        # Control: with nothing logged, the away +EV side alerts normally.
        self.assertEqual(self._run(cache, today_bets=[])["count"], 1)


class PropOppositeSuppressionTests(unittest.TestCase):
    def test_both_sides_prop_emits_single_best_side(self):
        with patch.object(unified_bot, "is_already_logged", return_value=False), \
                patch.object(unified_bot, "log_bet_to_db", return_value=True):
            alerts = unified_bot.evaluate_player_props(
                _both_sides_prop_event(),
                "basketball_nba",
                ["draftkings", "fanduel"],
                {},
            )
        self.assertEqual(len(alerts), 1)


if __name__ == "__main__":
    unittest.main()
