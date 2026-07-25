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

    def test_parse_mlb_prop_selection(self):
        spec = parse_selection("pitcher_outs", "Dustin May OVER 17.5")
        self.assertEqual(spec["type"], "player_prop")
        self.assertEqual(spec["player"], "Dustin May")
        self.assertEqual(spec["side"], "over")
        self.assertEqual(spec["line"], 17.5)

    def test_outcome_matches_prop_by_player_side_and_point(self):
        spec = parse_selection("pitcher_strikeouts", "Rhett Lowder OVER 3.5")
        # Same line, different players in the market — must match on description.
        self.assertTrue(
            outcome_matches(spec, {"name": "Over", "description": "Rhett Lowder", "point": 3.5})
        )
        self.assertFalse(
            outcome_matches(spec, {"name": "Over", "description": "Dustin May", "point": 3.5})
        )
        self.assertFalse(
            outcome_matches(spec, {"name": "Under", "description": "Rhett Lowder", "point": 3.5})
        )
        self.assertFalse(
            outcome_matches(spec, {"name": "Over", "description": "Rhett Lowder", "point": 5.5})
        )

    def test_outcome_matches_batter_prop_multiple_same_line(self):
        spec = parse_selection("batter_total_bases", "Ernie Clement OVER 1.5")
        self.assertTrue(
            outcome_matches(spec, {"name": "Over", "description": "Ernie Clement", "point": 1.5})
        )
        self.assertFalse(
            outcome_matches(spec, {"name": "Over", "description": "Vladimir Guerrero Jr", "point": 1.5})
        )


if __name__ == "__main__":
    unittest.main()
