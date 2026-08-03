import unittest
from unittest import mock

import clv_tracker
from services.bet_logic import parse_selection


def _prop_market(over_price, under_price, player="Wyatt Langford", line=1.5, key="batter_total_bases"):
    return {
        "key": key,
        "outcomes": [
            {"name": "Over", "description": player, "point": line, "price": over_price},
            {"name": "Under", "description": player, "point": line, "price": under_price},
        ],
    }


def _h2h_market(team, price):
    return {"key": "h2h", "outcomes": [{"name": team, "price": price}]}


class PropConsensusCloseTest(unittest.TestCase):
    def test_pinnacle_only_labels_pinnacle(self):
        game = {"bookmakers": [{"key": "pinnacle", "markets": [_prop_market(1.9, 1.95)]}]}
        spec = parse_selection("batter_total_bases", "Wyatt Langford Under 1.5")
        result = clv_tracker._prop_consensus_close(game, ["batter_total_bases"], spec)
        self.assertIsNotNone(result)
        fair_decimal, label = result
        self.assertEqual(label, "Pinnacle")
        self.assertGreater(fair_decimal, 1.0)

    def test_no_pinnacle_returns_none(self):
        game = {
            "bookmakers": [
                {"key": "bookmaker", "markets": [_prop_market(1.87, 1.95)]},
                {"key": "circa", "markets": [_prop_market(1.91, 1.90)]},
                {"key": "draftkings", "markets": [_prop_market(2.10, 1.72)]},
            ]
        }
        spec = parse_selection("batter_total_bases", "Wyatt Langford Over 1.5")
        result = clv_tracker._prop_consensus_close(game, ["batter_total_bases"], spec)
        self.assertIsNone(result)

    def test_no_sharp_book_returns_none(self):
        game = {"bookmakers": [{"key": "draftkings", "markets": [_prop_market(2.1, 1.72)]}]}
        spec = parse_selection("batter_total_bases", "Wyatt Langford Over 1.5")
        self.assertIsNone(clv_tracker._prop_consensus_close(game, ["batter_total_bases"], spec))

    def test_one_sided_market_returns_none(self):
        one_sided = {"key": "batter_total_bases", "outcomes": [
            {"name": "Over", "description": "Wyatt Langford", "point": 1.5, "price": 1.9},
        ]}
        game = {"bookmakers": [{"key": "pinnacle", "markets": [one_sided]}]}
        spec = parse_selection("batter_total_bases", "Wyatt Langford Over 1.5")
        self.assertIsNone(clv_tracker._prop_consensus_close(game, ["batter_total_bases"], spec))


class RunClvTrackerTest(unittest.TestCase):
    def _run(self, bets, cache):
        with mock.patch.object(clv_tracker, "get_all_bets", return_value=bets), \
             mock.patch.object(clv_tracker, "get_market_cache", return_value=cache), \
             mock.patch.object(clv_tracker, "update_bet_clv") as update, \
             mock.patch.object(clv_tracker, "_send_clv_update", return_value=False):
            result = clv_tracker.run_clv_tracker()
        return result, update

    def _bet(self, **kw):
        base = {
            "id": 1,
            "date": "2999-01-01",
            "result": "",
            "sport": "baseball_mlb",
            "event_id": "evt1",
            "odds": -110,
        }
        base.update(kw)
        return base

    def test_prop_bet_skipped_without_pinnacle(self):
        bet = self._bet(market="batter_total_bases", selection="Wyatt Langford Under 1.5")
        cache = {"baseball_mlb": [{
            "id": "evt1",
            "bookmakers": [
                {"key": "bookmaker", "markets": [_prop_market(1.87, 1.95)]},
                {"key": "circa", "markets": [_prop_market(1.91, 1.90)]},
            ],
        }]}
        result, update = self._run([bet], cache)
        self.assertEqual(result["count"], 0)
        update.assert_not_called()

    def test_prop_bet_with_pinnacle_tracked(self):
        bet = self._bet(market="pitcher_strikeouts", selection="Sonny Gray Over 4.5")
        cache = {"baseball_mlb": [{
            "id": "evt1",
            "bookmakers": [
                {"key": "pinnacle", "markets": [_prop_market(1.87, 1.95, player="Sonny Gray", line=4.5, key="pitcher_strikeouts")]},
            ],
        }]}
        result, update = self._run([bet], cache)
        self.assertEqual(result["count"], 1)
        update.assert_called_once()

    def test_main_market_pinnacle_clv(self):
        bet = self._bet(market="h2h", selection="Athletics")
        cache = {"baseball_mlb": [{
            "id": "evt1",
            "bookmakers": [{"key": "pinnacle", "markets": [_h2h_market("Athletics", 1.9)]}],
        }]}
        result, update = self._run([bet], cache)
        self.assertEqual(result["count"], 1)
        update.assert_called_once()

    def test_prop_without_sharp_line_skipped(self):
        bet = self._bet(market="batter_total_bases", selection="Wyatt Langford Under 1.5")
        cache = {"baseball_mlb": [{
            "id": "evt1",
            "bookmakers": [{"key": "draftkings", "markets": [_prop_market(2.1, 1.72)]}],
        }]}
        result, update = self._run([bet], cache)
        self.assertEqual(result["count"], 0)
        update.assert_not_called()

    def test_missing_event_skipped(self):
        bet = self._bet(market="h2h", selection="Athletics", event_id="missing")
        cache = {"baseball_mlb": [{"id": "evt1", "bookmakers": []}]}
        result, update = self._run([bet], cache)
        self.assertEqual(result["count"], 0)
        update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
