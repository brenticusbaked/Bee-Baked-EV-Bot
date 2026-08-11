import unittest
from unittest import mock

import db_manager
import execution_scanner
import unified_bot
from models import market_ev
from services import last_ten


def _rows(values, opponent=None):
    return [
        {
            "strikeouts": value,
            "game_date": f"2026-07-{index + 1:02d}",
            "opponent": opponent,
        }
        for index, value in enumerate(values)
    ]


class HitRateWindowTest(unittest.TestCase):
    """The opponent lookup over-fetches; the hit rate must still be last 10."""

    def test_only_ten_most_recent_games_are_counted(self):
        # 30 games available: the 10 most recent are all overs, the rest unders.
        rows = _rows([9] * 10 + [1] * 20, opponent="Chicago Cubs")
        with mock.patch.object(db_manager, "_safe_execute", return_value=rows):
            result = db_manager.get_l10_hit_rate(
                "Noah Cameron", "pitcher_strikeouts", 4.5, "baseball_mlb", opponent="Chicago Cubs"
            )
        self.assertEqual(result["games"], 10)
        self.assertEqual(result["over"], 10)
        self.assertEqual(result["under"], 0)

    def test_head_to_head_may_come_from_outside_the_window(self):
        rows = _rows([9] * 10, opponent=None) + _rows([2], opponent="Chicago Cubs")
        with mock.patch.object(db_manager, "_safe_execute", return_value=rows):
            result = db_manager.get_l10_hit_rate(
                "Noah Cameron", "pitcher_strikeouts", 4.5, "baseball_mlb", opponent="Chicago Cubs"
            )
        self.assertEqual(result["games"], 10)
        self.assertIsNotNone(result["last_vs_game"])
        self.assertEqual(result["last_vs_game"]["value"], 2.0)

    def test_whole_number_line_reports_pushes(self):
        rows = _rows([5, 5, 6, 4, 7])
        with mock.patch.object(db_manager, "_safe_execute", return_value=rows):
            result = db_manager.get_l10_hit_rate(
                "Noah Cameron", "pitcher_strikeouts", 5.0, "baseball_mlb"
            )
        self.assertEqual(result["over"], 2)
        self.assertEqual(result["under"], 1)
        self.assertEqual(result["push"], 2)
        self.assertEqual(result["games"], 5)

    def test_either_matchup_team_can_be_the_opponent(self):
        rows = _rows([3, 4], opponent="Seattle Mariners")
        with mock.patch.object(db_manager, "_safe_execute", return_value=rows):
            result = db_manager.get_l10_hit_rate(
                "Noah Cameron",
                "pitcher_strikeouts",
                4.5,
                "baseball_mlb",
                opponent=("Tampa Bay Rays", "Seattle Mariners"),
            )
        self.assertIsNotNone(result["last_vs_game"])
        self.assertEqual(result["last_vs_game"]["opponent"], "Seattle Mariners")


class SideResolutionTest(unittest.TestCase):
    def test_over_under_and_yes_no_resolve(self):
        self.assertEqual(last_ten.normalize_side("Over"), "over")
        self.assertEqual(last_ten.normalize_side("under"), "under")
        self.assertEqual(last_ten.normalize_side("Yes"), "over")
        self.assertEqual(last_ten.normalize_side("No"), "under")

    def test_team_name_is_not_read_as_a_direction(self):
        self.assertIsNone(last_ten.normalize_side("Seattle Storm"))

    def test_team_side_reports_form_instead_of_a_false_direction(self):
        result = {"over": 1, "under": 9, "games": 10, "values": [4.0] * 10, "last_game": None}
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=result):
            line = last_ten.build_last_ten_context_line(
                "Seattle Storm", "h2h", "", "Seattle Storm", "basketball_wnba", enabled=True
            )
        self.assertNotIn("stayed under", line)
        self.assertIn("averaging 4.00", line)

    def test_push_count_is_surfaced(self):
        result = {"over": 6, "under": 2, "push": 2, "games": 10, "values": [], "last_game": None}
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=result):
            line = last_ten.build_last_ten_context_line(
                "Noah Cameron", "pitcher_strikeouts", 5.0, "over", "baseball_mlb", enabled=True
            )
        self.assertIn("6/10", line)
        self.assertIn("(2 push)", line)

    def test_home_run_phrasing(self):
        result = {"over": 3, "under": 7, "games": 10, "values": [], "last_game": None}
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=result):
            line = last_ten.build_last_ten_context_line(
                "Kyle Schwarber", "batter_home_runs", 0.5, "over", "baseball_mlb", enabled=True
            )
        self.assertIn("homered in 3/10", line)


class PropOpponentTest(unittest.TestCase):
    def test_both_teams_offered_as_candidates(self):
        event = {"home_team": "New York Yankees", "away_team": "Boston Red Sox"}
        self.assertEqual(
            unified_bot._prop_opponent(event),
            ("New York Yankees", "Boston Red Sox"),
        )


class ExecutionDeskLastTenTest(unittest.TestCase):
    def _candidate(self, player="", outcome_name="Over"):
        return {
            "sport": "baseball_mlb",
            "matchup": "Kansas City Royals @ Los Angeles Dodgers",
            "market_type": "pitcher_strikeouts",
            "best": {
                "book": "FanDuel",
                "price": 2.26,
                "selection": "Noah Cameron Under 4.5",
                "player": player,
                "outcome_name": outcome_name,
                "point": 4.5,
            },
            "fair_decimal": 2.2,
            "edge": 0.03,
            "units": 1.0,
        }

    def test_prop_alert_includes_last_ten(self):
        result = {"over": 8, "under": 2, "games": 10, "values": [], "last_game": None}
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=result):
            text = execution_scanner._execution_last_ten(self._candidate(player="Noah Cameron"))
        self.assertIn("Noah Cameron", text)
        self.assertIn("8/10", text)

    def test_lookup_uses_the_player_not_the_outcome_label(self):
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=None) as lookup:
            execution_scanner._execution_last_ten(self._candidate(player="Noah Cameron"))
        self.assertEqual(lookup.call_args.args[0], "Noah Cameron")
        self.assertEqual(
            lookup.call_args.kwargs["opponent"],
            ("Los Angeles Dodgers", "Kansas City Royals"),
        )

    def test_description_carries_the_line(self):
        result = {"over": 8, "under": 2, "games": 10, "values": [], "last_game": None}
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=result):
            description = execution_scanner._execution_desk_alert_description(
                self._candidate(player="Noah Cameron")
            )
        self.assertIn("**Last 10:**", description)


class ModelOverlayLastTenTest(unittest.TestCase):
    def _edge(self):
        return market_ev.MarketEdge(
            sport="americanfootball_nfl",
            event_id="evt",
            matchup="Cincinnati Bengals @ Cleveland Browns",
            market_key="player_anytime_td",
            selection="Ja'Marr Chase Yes",
            player="Ja'Marr Chase",
            point=None,
            book_key="draftkings",
            book_title="DraftKings",
            offered_decimal=2.1,
            fair_probability=0.55,
            edge=0.155,
            units=1.0,
            side="Yes",
        )

    def test_embed_gains_a_last_ten_field(self):
        result = {"over": 4, "under": 6, "games": 10, "values": [], "last_game": None}
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=result):
            embed = market_ev.build_embed(self._edge())
        names = [field["name"] for field in embed["fields"]]
        self.assertIn("Last 10", names)

    def test_field_omitted_when_no_stat_history(self):
        with mock.patch.object(db_manager, "get_l10_hit_rate", return_value=None):
            embed = market_ev.build_embed(self._edge())
        names = [field["name"] for field in embed["fields"]]
        self.assertNotIn("Last 10", names)
        # The mandated embed sections must survive the optional field.
        self.assertIn("The Math", names)


if __name__ == "__main__":
    unittest.main()
