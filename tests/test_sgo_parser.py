import unittest
from unittest import mock

import requests

import bot_propodds_nba as prop_bot


def _mlb_event():
    return {
        "eventID": "evt_mlb",
        "leagueID": "MLB",
        "teams": {
            "home": {"teamID": "STL", "names": {"long": "St. Louis Cardinals"}},
            "away": {"teamID": "CIN", "names": {"long": "Cincinnati Reds"}},
        },
        "players": {
            "DUSTIN_MAY_1_MLB": {"name": "Dustin May", "teamID": "CIN"},
        },
        "odds": {
            "strikeouts-DUSTIN_MAY_1_MLB-game-ou-over": {
                "oddID": "strikeouts-DUSTIN_MAY_1_MLB-game-ou-over",
                "statID": "strikeouts",
                "statEntityID": "DUSTIN_MAY_1_MLB",
                "periodID": "game",
                "betTypeID": "ou",
                "sideID": "over",
                "byBookmaker": {
                    "pinnacle": {"odds": -110, "overUnder": "5.5", "available": True},
                    "draftkings": {"odds": 130, "overUnder": "5.5", "available": True},
                },
            },
            "strikeouts-DUSTIN_MAY_1_MLB-game-ou-under": {
                "oddID": "strikeouts-DUSTIN_MAY_1_MLB-game-ou-under",
                "statID": "strikeouts",
                "statEntityID": "DUSTIN_MAY_1_MLB",
                "periodID": "game",
                "betTypeID": "ou",
                "sideID": "under",
                "byBookmaker": {
                    "pinnacle": {"odds": -110, "overUnder": "5.5", "available": True},
                    "draftkings": {"odds": -110, "overUnder": "5.5", "available": True},
                },
            },
            "points-away-game-ml-away": {
                "statID": "points",
                "statEntityID": "away",
                "betTypeID": "ml",
                "sideID": "away",
                "byBookmaker": {"pinnacle": {"odds": 130, "available": True}},
            },
        },
    }


class SgoParserTests(unittest.TestCase):
    def test_matchup_from_teams(self):
        self.assertEqual(
            prop_bot._matchup_from_event(_mlb_event()),
            "Cincinnati Reds @ St. Louis Cardinals",
        )

    def test_over_under_parses_one_offer_per_book(self):
        event = _mlb_event()
        odd_obj = event["odds"]["strikeouts-DUSTIN_MAY_1_MLB-game-ou-over"]
        offers = prop_bot._parse_prop_offers(odd_obj, event["players"])
        self.assertEqual(len(offers), 2)
        by_book = {offer["book"]: offer for offer in offers}
        self.assertEqual(set(by_book), {"pinnacle", "draftkings"})
        self.assertEqual(by_book["pinnacle"]["player"], "Dustin May")
        self.assertEqual(by_book["pinnacle"]["stat"], "strikeouts")
        self.assertEqual(by_book["pinnacle"]["side"], "over")
        self.assertEqual(by_book["pinnacle"]["line"], "5.5")

    def test_team_moneyline_is_ignored(self):
        event = _mlb_event()
        odd_obj = event["odds"]["points-away-game-ml-away"]
        self.assertEqual(prop_bot._parse_prop_offers(odd_obj, event["players"]), [])

    def test_unavailable_book_is_skipped(self):
        odd_obj = {
            "statID": "strikeouts",
            "statEntityID": "DUSTIN_MAY_1_MLB",
            "betTypeID": "ou",
            "sideID": "over",
            "byBookmaker": {
                "pinnacle": {"odds": -110, "overUnder": "5.5", "available": False},
                "fanduel": {"odds": -105, "overUnder": "5.5", "available": True},
            },
        }
        offers = prop_bot._parse_prop_offers(odd_obj, {"DUSTIN_MAY_1_MLB": {"name": "Dustin May"}})
        self.assertEqual([offer["book"] for offer in offers], ["fanduel"])

    def test_unsupported_stat_returns_no_offers(self):
        odd_obj = {
            "statID": "double_plays_turned",
            "statEntityID": "SOME_PLAYER_1_MLB",
            "betTypeID": "ou",
            "sideID": "over",
            "byBookmaker": {"pinnacle": {"odds": -110, "overUnder": "1.5", "available": True}},
        }
        self.assertEqual(prop_bot._parse_prop_offers(odd_obj, {}), [])

    def test_camelcase_statid_normalizes(self):
        self.assertEqual(prop_bot._normalize_stat_name("homeRuns"), "home_runs")
        self.assertEqual(prop_bot._normalize_stat_name("totalBases"), "total_bases")

    def test_nfl_stats_supported(self):
        for stat in ("passing_yards", "rushing_yards", "receiving_yards", "receptions"):
            self.assertIn(stat, prop_bot.TARGET_STATS)

    def test_player_resolved_from_players_map(self):
        self.assertEqual(
            prop_bot._resolve_player_name("DUSTIN_MAY_1_MLB", {"DUSTIN_MAY_1_MLB": {"name": "Dustin May"}}),
            "Dustin May",
        )

    def test_player_falls_back_to_deslugged_id(self):
        self.assertEqual(prop_bot._resolve_player_name("RHETT_LOWDER_1_MLB", {}), "Rhett Lowder")

    def test_wnba_is_opt_in_by_default(self):
        with mock.patch.dict(prop_bot.os.environ, {"PLAYER_PROP_LEAGUES": "NBA,WNBA,MLB,NFL"}, clear=False):
            leagues = prop_bot._parse_player_prop_leagues()
        self.assertNotIn("WNBA", leagues)

    def test_wnba_is_included_when_explicitly_enabled(self):
        with mock.patch.dict(
            prop_bot.os.environ,
            {"PLAYER_PROP_LEAGUES": "NBA,WNBA,MLB,NFL", "ENABLE_WNBA_PROP_BOT": "true"},
            clear=False,
        ):
            leagues = prop_bot._parse_player_prop_leagues()
        self.assertIn("WNBA", leagues)


class SgoConsensusTests(unittest.TestCase):
    def test_pinnacle_first_uses_pinnacle_alone(self):
        sharp = {
            "pinnacle": {"over": {"price": 1.90}, "under": {"price": 1.90}},
            "circa": {"over": {"price": 1.50}, "under": {"price": 2.50}},
        }
        probabilities, source, book_count = prop_bot._consensus_from_sharp_books(sharp, "points", "25.5")
        self.assertEqual(source.split("_")[0], "pinnacle")
        self.assertEqual(book_count, 1)
        self.assertAlmostEqual(probabilities["over"], 0.5, places=6)

    def test_falls_back_to_sharp_consensus_without_pinnacle(self):
        sharp = {
            "circa": {"over": {"price": 1.90}, "under": {"price": 1.90}},
            "cris": {"over": {"price": 1.90}, "under": {"price": 1.90}},
        }
        probabilities, source, book_count = prop_bot._consensus_from_sharp_books(sharp, "points", "25.5")
        self.assertTrue(source.startswith("consensus"))
        self.assertEqual(book_count, 2)
        self.assertIn("over", probabilities)


class LeagueFetchToleranceTests(unittest.TestCase):
    """A failing/unsupported league (e.g. SGO 400 on WNBA) must only skip that
    league — never abort the scan and starve the working leagues of props."""

    def _run(self, leagues, responder):
        with mock.patch.object(prop_bot, "SGO_API_KEY", "test-key"), \
             mock.patch.object(prop_bot, "PLAYER_PROP_LEAGUES", leagues), \
             mock.patch.object(prop_bot, "get_book_weights", return_value={"draftkings": 1.0}), \
             mock.patch.object(prop_bot, "is_already_logged", return_value=False), \
             mock.patch.object(prop_bot, "log_bet_to_db", return_value=True), \
             mock.patch.object(prop_bot, "request", side_effect=responder):
            return prop_bot.get_sgo_edges()

    def test_bad_league_does_not_abort_remaining_leagues(self):
        def responder(method, url, params=None, **kwargs):
            league = (params or {}).get("leagueID")
            if league == "WNBA":
                resp = requests.Response()
                resp.status_code = 400
                resp._content = b'{"success":false,"error":"unsupported league"}'
                raise requests.HTTPError("400 Bad Request", response=resp)
            fake = mock.Mock()
            fake.json.return_value = {"success": True, "data": [_mlb_event()]}
            return fake

        picks, _near, stats = self._run(["WNBA", "MLB"], responder)
        self.assertGreater(stats["parsed_props"], 0)
        self.assertGreater(stats["events"], 0)
        self.assertEqual(len(picks), 1)
        self.assertNotIn("WNBA:400", stats.get("errored_leagues", []))
        self.assertNotIn("reason", stats)

    def test_retry_error_is_soft_skipped(self):
        def responder(method, url, params=None, **kwargs):
            league = (params or {}).get("leagueID")
            if league == "NBA":
                raise requests.exceptions.RetryError("retry exhausted")
            fake = mock.Mock()
            fake.json.return_value = {"success": True, "data": [_mlb_event()]}
            return fake

        picks, _near, stats = self._run(["NBA", "MLB"], responder)
        self.assertGreater(stats["events"], 0)
        self.assertEqual(len(picks), 1)
        self.assertIn("NBA:retry", stats.get("soft_skipped_leagues", []))
        self.assertNotIn("NBA:RetryError", stats.get("errored_leagues", []))


if __name__ == "__main__":
    unittest.main()