import unittest
from unittest import mock

import db_manager
import unified_bot
from services import stat_ingest


class StatLogTableTest(unittest.TestCase):
    def test_direct_sport_mapping(self):
        self.assertEqual(db_manager._stat_log_table("baseball_mlb"), "mlb_player_logs")
        self.assertEqual(db_manager._stat_log_table("basketball_wnba"), "wnba_player_logs")
        self.assertEqual(db_manager._stat_log_table("tennis_atp"), "tennis_match_logs")

    def test_soccer_prefix_mapping(self):
        self.assertEqual(db_manager._stat_log_table("soccer_epl"), "soccer_player_logs")
        self.assertEqual(db_manager._stat_log_table("soccer_usa_mls"), "soccer_player_logs")

    def test_unknown_sport_returns_none(self):
        self.assertIsNone(db_manager._stat_log_table("cricket_ipl"))


class StatValueForPropTest(unittest.TestCase):
    def test_prefers_dedicated_column(self):
        row = {"total_bases": 2, "stats": {"total_bases": 9}}
        self.assertEqual(db_manager._stat_value_for_prop(row, "batter_total_bases"), 2.0)

    def test_falls_back_to_stats_jsonb(self):
        row = {"stats": {"total_bases": 3}}
        self.assertEqual(db_manager._stat_value_for_prop(row, "batter_total_bases"), 3.0)

    def test_missing_metric_returns_none(self):
        self.assertIsNone(db_manager._stat_value_for_prop({"stats": {}}, "batter_hits"))


class GetL10HitRateTest(unittest.TestCase):
    def _rows(self):
        # 5 recent games of total bases: 2, 1, 0, 3, 2
        return [{"total_bases": v} for v in (2, 1, 0, 3, 2)]

    def test_over_under_counts(self):
        with mock.patch.object(db_manager, "_safe_execute", return_value=self._rows()):
            result = db_manager.get_l10_hit_rate("Wyatt Langford", "batter_total_bases", 1.5, "baseball_mlb")
        self.assertIsNotNone(result)
        self.assertEqual(result["games"], 5)
        self.assertEqual(result["over"], 3)   # 2, 3, 2 are > 1.5
        self.assertEqual(result["under"], 2)  # 1, 0 are < 1.5

    def test_untracked_sport_returns_none(self):
        result = db_manager.get_l10_hit_rate("X", "batter_hits", 0.5, "cricket_ipl")
        self.assertIsNone(result)

    def test_no_rows_returns_none(self):
        with mock.patch.object(db_manager, "_safe_execute", return_value=None):
            result = db_manager.get_l10_hit_rate("X", "batter_hits", 0.5, "baseball_mlb")
        self.assertIsNone(result)


class L10ContextLineTest(unittest.TestCase):
    def test_over_context_formatting(self):
        with mock.patch.object(unified_bot, "ENABLE_L10_CONTEXT", True), \
             mock.patch.object(
                 unified_bot, "get_l10_hit_rate",
                 return_value={"over": 7, "under": 3, "games": 10, "line": 1.5},
             ):
            line = unified_bot._l10_context_line("Wyatt Langford", "batter_total_bases", 1.5, "over", "baseball_mlb")
        self.assertIn("7/10", line)
        self.assertIn("cleared", line)
        self.assertIn("Wyatt Langford", line)

    def test_disabled_returns_empty(self):
        with mock.patch.object(unified_bot, "ENABLE_L10_CONTEXT", False):
            self.assertEqual(unified_bot._l10_context_line("X", "batter_hits", 0.5, "over", "baseball_mlb"), "")

    def test_no_data_returns_empty(self):
        with mock.patch.object(unified_bot, "ENABLE_L10_CONTEXT", True), \
             mock.patch.object(unified_bot, "get_l10_hit_rate", return_value=None):
            self.assertEqual(unified_bot._l10_context_line("X", "batter_hits", 0.5, "over", "baseball_mlb"), "")


class SoccerRoutingTest(unittest.TestCase):
    def test_soccer_webhook_prefix(self):
        self.assertEqual(unified_bot.webhook_for_sport("soccer_epl"), unified_bot.SOCCER_ALERT_WEBHOOK)

    def test_soccer_market_gate(self):
        with mock.patch.object(unified_bot, "ENABLE_SOCCER_ALERTS", True):
            self.assertTrue(unified_bot._market_allowed_for_sport("soccer_epl", "h2h"))
            self.assertTrue(unified_bot._market_allowed_for_sport("soccer_spain_la_liga", "totals"))
        with mock.patch.object(unified_bot, "ENABLE_SOCCER_ALERTS", False):
            self.assertFalse(unified_bot._market_allowed_for_sport("soccer_epl", "h2h"))


class MlbBoxscoreParseTest(unittest.TestCase):
    def test_ip_to_outs(self):
        self.assertEqual(stat_ingest._ip_to_outs("6.2"), 20)
        self.assertEqual(stat_ingest._ip_to_outs(5.0), 15)
        self.assertEqual(stat_ingest._ip_to_outs(0.1), 1)
        self.assertIsNone(stat_ingest._ip_to_outs(None))

    def test_parse_batter_and_pitcher(self):
        box = {
            "home": {
                "team": {"abbreviation": "TEX"},
                "players": {
                    "ID1": {
                        "person": {"fullName": "Wyatt Langford"},
                        "stats": {"batting": {"hits": 2, "doubles": 1, "triples": 0, "homeRuns": 1, "rbi": 3}},
                    }
                },
            },
            "away": {
                "team": {"abbreviation": "SEA"},
                "players": {
                    "ID2": {
                        "person": {"fullName": "Logan Gilbert"},
                        "stats": {"pitching": {"strikeOuts": 8, "inningsPitched": "6.2", "hits": 4}},
                    },
                    "ID3": {"person": {"fullName": "Nobody"}, "stats": {"batting": {}, "pitching": {}}},
                },
            },
        }
        rows = stat_ingest._parse_mlb_boxscore(box, "2026-07-25")
        by_name = {r["player_name"]: r for r in rows}
        self.assertIn("Wyatt Langford", by_name)
        self.assertIn("Logan Gilbert", by_name)
        self.assertNotIn("Nobody", by_name)  # no batting/pitching -> skipped
        # TB = hits + doubles + 2*triples + 3*HR = 2 + 1 + 0 + 3 = 6
        self.assertEqual(by_name["Wyatt Langford"]["total_bases"], 6)
        self.assertEqual(by_name["Wyatt Langford"]["home_runs"], 1)
        self.assertEqual(by_name["Logan Gilbert"]["strikeouts"], 8)
        self.assertEqual(by_name["Logan Gilbert"]["outs"], 20)
        self.assertEqual(by_name["Logan Gilbert"]["hits_allowed"], 4)


class EspnBasketballParseTest(unittest.TestCase):
    def _summary(self):
        keys = [
            "minutes",
            "fieldGoalsMade-fieldGoalsAttempted",
            "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
            "freeThrowsMade-freeThrowsAttempted",
            "offensiveRebounds",
            "defensiveRebounds",
            "rebounds",
            "assists",
            "steals",
            "blocks",
            "turnovers",
            "fouls",
            "plusMinus",
            "points",
        ]
        return {
            "boxscore": {
                "players": [
                    {
                        "team": {"abbreviation": "LV"},
                        "statistics": [
                            {
                                "keys": keys,
                                "athletes": [
                                    {
                                        "athlete": {"id": "1", "displayName": "A'ja Wilson"},
                                        "stats": [
                                            "34", "10-18", "1-2", "6-7",
                                            "3", "7", "10", "4", "2", "3", "2", "1", "5", "27",
                                        ],
                                    },
                                    {
                                        "athlete": {"id": "2", "displayName": "Did Not Play"},
                                        "didNotPlay": True,
                                        "stats": [],
                                    },
                                ],
                            }
                        ],
                    }
                ]
            }
        }

    def test_parse_maps_stat_keys(self):
        rows = stat_ingest._parse_espn_basketball_summary(
            self._summary(), stat_ingest.date(2026, 7, 25), "basketball_wnba"
        )
        by_name = {r["player_name"]: r for r in rows}
        self.assertIn("A'ja Wilson", by_name)
        self.assertNotIn("Did Not Play", by_name)  # DNP skipped
        row = by_name["A'ja Wilson"]
        self.assertEqual(row["points"], 27.0)
        self.assertEqual(row["rebounds"], 10.0)
        self.assertEqual(row["assists"], 4.0)
        self.assertEqual(row["threes_made"], 1.0)  # from "1-2"
        self.assertEqual(row["team"], "LV")
        self.assertEqual(row["league"], "basketball_wnba")

    def test_espn_made_parsing(self):
        self.assertEqual(stat_ingest._espn_made("7-12"), 7.0)
        self.assertEqual(stat_ingest._espn_made("0-0"), 0.0)
        self.assertIsNone(stat_ingest._espn_made(None))


class SoccerLeagueDefaultTest(unittest.TestCase):
    def test_blank_env_falls_back_to_default(self):
        import importlib

        with mock.patch.dict("os.environ", {"SOCCER_STAT_LEAGUES": ""}, clear=False):
            reloaded = importlib.reload(stat_ingest)
            self.assertTrue(reloaded.SOCCER_STAT_LEAGUES)  # not wiped by blank env
        importlib.reload(stat_ingest)  # restore module state for other tests


class IngestAllTest(unittest.TestCase):
    def test_ingest_all_isolates_and_returns_counts(self):
        # All library-backed fetchers return [] when the lib is absent; force it
        # so the job is deterministic and upsert is exercised for one table.
        with mock.patch.object(stat_ingest, "fetch_mlb_logs", return_value=[{"player_name": "A", "total_bases": 2}]), \
             mock.patch.object(stat_ingest, "fetch_nba_logs", return_value=[]), \
             mock.patch.object(stat_ingest, "fetch_nfl_logs", return_value=[]), \
             mock.patch.object(stat_ingest, "fetch_soccer_logs", return_value=[]), \
             mock.patch.object(stat_ingest, "fetch_tennis_logs", return_value=[]), \
             mock.patch.object(stat_ingest.db_manager, "upsert_player_logs", return_value=1) as upsert:
            results = stat_ingest.ingest_all()
        self.assertEqual(results.get("mlb_player_logs"), 1)
        upsert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
