import unittest

from services.bet_logic import grade_game_bet, outcome_matches, parse_selection


class BetLogicTests(unittest.TestCase):
    def test_parse_spread_selection(self):
        spec = parse_selection("MODEL_NBA_SPREAD", "Chicago Bulls -4.5")
        self.assertEqual(spec["type"], "spread")
        self.assertEqual(spec["team"], "Chicago Bulls")
        self.assertEqual(spec["line"], -4.5)

    def test_outcome_matches_spread_name_and_point(self):
        spec = parse_selection("spreads", "Chicago Bulls -4.5")
        outcome = {"name": "Chicago Bulls", "point": -4.5}
        self.assertTrue(outcome_matches(spec, outcome))

    def test_outcome_matches_total_requires_side_and_point(self):
        spec = parse_selection("totals", "Over 221.5")
        self.assertTrue(outcome_matches(spec, {"name": "Over", "point": 221.5}))
        self.assertFalse(outcome_matches(spec, {"name": "Under", "point": 221.5}))

    def test_grade_h2h(self):
        result = grade_game_bet("h2h", "Chicago Bulls", "Chicago Bulls @ Miami Heat", {"Chicago Bulls": 109, "Miami Heat": 101})
        self.assertEqual(result, "WIN")

    def test_grade_spread(self):
        result = grade_game_bet(
            "MODEL_NBA_SPREAD",
            "Chicago Bulls -4.5",
            "Chicago Bulls @ Miami Heat",
            {"Chicago Bulls": 110, "Miami Heat": 100},
        )
        self.assertEqual(result, "WIN")

    def test_grade_total_push(self):
        result = grade_game_bet("totals", "Over 210", "Chicago Bulls @ Miami Heat", {"Chicago Bulls": 100, "Miami Heat": 110})
        self.assertEqual(result, "PUSH")


if __name__ == "__main__":
    unittest.main()
