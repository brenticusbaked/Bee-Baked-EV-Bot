"""Tests for the Hive Scanner mathematical primitives and scanning logic."""

import os
import tempfile
import time
import unittest

from hive_scanner import (
    EMBED_COLOR,
    american_to_decimal,
    build_discord_embed,
    calculate_ev,
    decimal_to_american,
    devig,
    filter_new_alerts,
    hours_until_next_game,
    implied_probability,
    multiplicative_devig,
    polling_interval_seconds,
    power_devig,
    quarter_kelly,
    should_poll_sport,
    _devig_market,
    _extract_pinnacle_lines,
    _get_cached_odds,
    _prune_seen_bets,
    _seen_bet_hash,
    _store_cached_odds,
)


# ---------------------------------------------------------------------------
# 1. Odds Conversion
# ---------------------------------------------------------------------------


class TestOddsConversion(unittest.TestCase):
    def test_american_to_decimal_positive(self):
        self.assertAlmostEqual(american_to_decimal(150), 2.5)

    def test_american_to_decimal_negative(self):
        self.assertAlmostEqual(american_to_decimal(-200), 1.5)

    def test_american_to_decimal_large_underdog(self):
        self.assertAlmostEqual(american_to_decimal(300), 4.0)

    def test_american_to_decimal_heavy_favourite(self):
        self.assertAlmostEqual(american_to_decimal(-500), 1.2)

    def test_american_to_decimal_zero_raises(self):
        with self.assertRaises(ValueError):
            american_to_decimal(0)

    def test_decimal_to_american_positive(self):
        self.assertEqual(decimal_to_american(2.5), "+150")

    def test_decimal_to_american_negative(self):
        self.assertEqual(decimal_to_american(1.5), "-200")

    def test_implied_probability(self):
        self.assertAlmostEqual(implied_probability(2.0), 0.5)
        self.assertAlmostEqual(implied_probability(4.0), 0.25)
        self.assertAlmostEqual(implied_probability(1.25), 0.8)

    def test_implied_probability_edge_cases(self):
        self.assertEqual(implied_probability(1.0), 0.0)
        self.assertEqual(implied_probability(0.5), 0.0)


# ---------------------------------------------------------------------------
# 2. De-Vigging (THE critical math)
# ---------------------------------------------------------------------------


class TestDevig(unittest.TestCase):
    def test_multiplicative_equal_odds(self):
        """Two sides at 1.91 each => ~52.36% implied each => devig to 50/50."""
        probs = [implied_probability(1.91), implied_probability(1.91)]
        fair = multiplicative_devig(probs)
        self.assertAlmostEqual(fair[0], 0.5, places=4)
        self.assertAlmostEqual(fair[1], 0.5, places=4)
        self.assertAlmostEqual(sum(fair), 1.0)

    def test_multiplicative_unequal(self):
        """-150 / +130 => de-vigged probabilities sum to 1."""
        probs = [implied_probability(american_to_decimal(-150)),
                 implied_probability(american_to_decimal(130))]
        fair = multiplicative_devig(probs)
        self.assertAlmostEqual(sum(fair), 1.0)
        self.assertGreater(fair[0], fair[1])

    def test_power_devig_equal_odds(self):
        probs = [implied_probability(1.91), implied_probability(1.91)]
        fair = power_devig(probs)
        self.assertAlmostEqual(fair[0], 0.5, places=6)
        self.assertAlmostEqual(fair[1], 0.5, places=6)
        self.assertAlmostEqual(sum(fair), 1.0, places=8)

    def test_power_devig_removes_vig(self):
        """-200 / +170 => overround removed."""
        dec_fav = american_to_decimal(-200)   # 1.5
        dec_dog = american_to_decimal(170)    # 2.7
        probs = [implied_probability(dec_fav), implied_probability(dec_dog)]
        # Raw sum > 1.0 (has vig)
        self.assertGreater(sum(probs), 1.0)
        fair = power_devig(probs)
        self.assertAlmostEqual(sum(fair), 1.0, places=8)
        # Favorite still more likely
        self.assertGreater(fair[0], fair[1])

    def test_devig_dispatch_multiplicative(self):
        probs = [0.55, 0.55]
        fair = devig(probs, method="multiplicative")
        self.assertAlmostEqual(fair[0], 0.5)

    def test_devig_dispatch_power(self):
        probs = [implied_probability(1.91), implied_probability(1.91)]
        fair = devig(probs, method="power")
        self.assertAlmostEqual(sum(fair), 1.0, places=8)

    def test_devig_realistic_pinnacle_line(self):
        """Pinnacle NBA spread: -110 / -110 => true prob 50/50."""
        dec = american_to_decimal(-110)  # 1.909...
        probs = [implied_probability(dec), implied_probability(dec)]
        fair = devig(probs, method="power")
        self.assertAlmostEqual(fair[0], 0.5, places=4)
        self.assertAlmostEqual(fair[1], 0.5, places=4)

    def test_devig_pinnacle_heavy_fav(self):
        """Pinnacle: -300 / +250.  True prob should be ~74-75% / ~25-26%."""
        dec_fav = american_to_decimal(-300)  # 1.333...
        dec_dog = american_to_decimal(250)   # 3.5
        probs = [implied_probability(dec_fav), implied_probability(dec_dog)]
        fair = devig(probs, method="power")
        self.assertAlmostEqual(sum(fair), 1.0, places=8)
        self.assertGreater(fair[0], 0.70)
        self.assertLess(fair[0], 0.80)


# ---------------------------------------------------------------------------
# 3. EV Calculation
# ---------------------------------------------------------------------------


class TestEVCalculation(unittest.TestCase):
    def test_positive_ev(self):
        """If true prob = 55%, offered decimal = 2.0 (+100), EV = 0.55*2 - 1 = 0.10."""
        ev = calculate_ev(0.55, 2.0)
        self.assertAlmostEqual(ev, 0.10)

    def test_negative_ev(self):
        """If true prob = 45%, offered decimal = 2.0, EV = 0.45*2 - 1 = -0.10."""
        ev = calculate_ev(0.45, 2.0)
        self.assertAlmostEqual(ev, -0.10)

    def test_zero_ev(self):
        """If true prob = 50%, offered decimal = 2.0, EV = 0."""
        ev = calculate_ev(0.50, 2.0)
        self.assertAlmostEqual(ev, 0.0)

    def test_ev_with_juice(self):
        """True prob = 52.4%, FanDuel offers -105 (1.952), should be +EV."""
        true_prob = 0.524
        offered = american_to_decimal(-105)  # 1.952...
        ev = calculate_ev(true_prob, offered)
        self.assertGreater(ev, 0.0)

    def test_ev_against_true_line_is_negative(self):
        """Betting at the true fair odds should yield ~0 EV."""
        true_prob = 0.6
        fair_decimal = 1.0 / true_prob  # ~1.667
        ev = calculate_ev(true_prob, fair_decimal)
        self.assertAlmostEqual(ev, 0.0, places=10)


# ---------------------------------------------------------------------------
# 4. Quarter-Kelly Sizing
# ---------------------------------------------------------------------------


class TestQuarterKelly(unittest.TestCase):
    def test_basic_quarter_kelly(self):
        """Known example: 55% true prob, +100 (2.0 decimal).
        b = 1.0, full Kelly = (1*0.55 - 0.45)/1 = 0.10
        Quarter Kelly = 0.10 / 4 = 0.025 = 2.5% of bankroll.
        """
        qk = quarter_kelly(0.55, 2.0)
        self.assertAlmostEqual(qk, 2.5)

    def test_quarter_kelly_larger_edge(self):
        """60% true prob, +100 (2.0). Full Kelly = 0.20, QK = 5.0%."""
        qk = quarter_kelly(0.60, 2.0)
        self.assertAlmostEqual(qk, 5.0)

    def test_quarter_kelly_negative_ev_returns_zero(self):
        """45% true prob, +100 (2.0). Full Kelly < 0 => 0."""
        qk = quarter_kelly(0.45, 2.0)
        self.assertEqual(qk, 0.0)

    def test_quarter_kelly_capped(self):
        """Very large edge should be capped at KELLY_CAP (default 5%)."""
        qk = quarter_kelly(0.90, 2.0)
        self.assertLessEqual(qk, 5.0)

    def test_quarter_kelly_underdog(self):
        """52% true prob, +200 (3.0). b=2, full Kelly = (2*0.52-0.48)/2 = 0.28,
        QK = 0.07 = 7% but capped at 5%.
        """
        qk = quarter_kelly(0.52, 3.0)
        self.assertLessEqual(qk, 5.0)
        self.assertGreater(qk, 0.0)


# ---------------------------------------------------------------------------
# 5. End-to-End De-Vig Pipeline
# ---------------------------------------------------------------------------


class TestEndToEndDevig(unittest.TestCase):
    def test_devig_market_two_way(self):
        """Simulate a Pinnacle market: -150 (1.667) / +130 (2.30)."""
        prices = {
            ("team a", ""): american_to_decimal(-150),
            ("team b", ""): american_to_decimal(130),
        }
        true_probs = _devig_market(prices)
        self.assertAlmostEqual(sum(true_probs.values()), 1.0, places=8)
        self.assertGreater(true_probs[("team a", "")], true_probs[("team b", "")])

    def test_full_pipeline_ev_check(self):
        """Pinnacle: -150/+130 => de-vig => compare FanDuel +160 on underdog.

        With power devig, the true fair line for team B is around +133, so the
        soft book needs to offer meaningfully better odds to be +EV.
        """
        pinnacle_prices = {
            ("team a", ""): american_to_decimal(-150),
            ("team b", ""): american_to_decimal(130),
        }
        true_probs = _devig_market(pinnacle_prices)

        # FanDuel offers +160 on team b (decimal 2.60) - clearly above fair
        fanduel_odds = american_to_decimal(160)
        true_prob_b = true_probs[("team b", "")]

        ev = calculate_ev(true_prob_b, fanduel_odds)
        # +160 vs true ~+133 => should be solidly +EV
        self.assertGreater(ev, 0.0)

        # Quarter Kelly should be positive
        qk = quarter_kelly(true_prob_b, fanduel_odds)
        self.assertGreater(qk, 0.0)

    def test_no_ev_when_line_is_worse(self):
        """If soft book offers worse odds than fair value, EV should be negative."""
        pinnacle_prices = {
            ("team a", ""): american_to_decimal(-150),
            ("team b", ""): american_to_decimal(130),
        }
        true_probs = _devig_market(pinnacle_prices)

        # Soft book offers +110 on team b (worse than true ~+130)
        soft_odds = american_to_decimal(110)
        true_prob_b = true_probs[("team b", "")]
        ev = calculate_ev(true_prob_b, soft_odds)
        self.assertLess(ev, 0.0)


# ---------------------------------------------------------------------------
# 6. Extract Pinnacle Lines
# ---------------------------------------------------------------------------


class TestExtractPinnacle(unittest.TestCase):
    def test_extract_from_api_response(self):
        events = [
            {
                "id": "abc123",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Team A", "price": 1.667},
                                    {"name": "Team B", "price": 2.30},
                                ],
                            }
                        ],
                    },
                    {
                        "key": "fanduel",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Team A", "price": 1.60},
                                    {"name": "Team B", "price": 2.40},
                                ],
                            }
                        ],
                    },
                ],
            }
        ]
        result = _extract_pinnacle_lines(events)
        self.assertIn("abc123", result)
        self.assertIn("h2h", result["abc123"])
        self.assertEqual(result["abc123"]["h2h"][("team a", "")], 1.667)
        self.assertEqual(result["abc123"]["h2h"][("team b", "")], 2.30)
        # FanDuel should NOT appear
        self.assertEqual(len(result["abc123"]["h2h"]), 2)

    def test_extract_spread_market(self):
        """Pinnacle spread lines should be extracted with point values."""
        events = [
            {
                "id": "spread1",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "spreads",
                                "outcomes": [
                                    {"name": "Team A", "price": 1.91, "point": -3.5},
                                    {"name": "Team B", "price": 1.91, "point": 3.5},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        result = _extract_pinnacle_lines(events)
        self.assertIn("spreads", result["spread1"])
        self.assertIn(("team a", "-3.5"), result["spread1"]["spreads"])
        self.assertIn(("team b", "3.5"), result["spread1"]["spreads"])

    def test_extract_totals_market(self):
        """Pinnacle totals lines should be extracted with point values."""
        events = [
            {
                "id": "total1",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 1.87, "point": 215.5},
                                    {"name": "Under", "price": 1.95, "point": 215.5},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        result = _extract_pinnacle_lines(events)
        self.assertIn("totals", result["total1"])
        self.assertIn(("over", "215.5"), result["total1"]["totals"])
        self.assertIn(("under", "215.5"), result["total1"]["totals"])


# ---------------------------------------------------------------------------
# 7. Dynamic Polling Scheduler
# ---------------------------------------------------------------------------


class TestPollingScheduler(unittest.TestCase):
    def test_post_game_skipped(self):
        """Negative hours => no polling."""
        self.assertIsNone(polling_interval_seconds(-1))

    def test_far_future_skipped(self):
        """> 24h out => no polling."""
        self.assertIsNone(polling_interval_seconds(30))

    def test_12_to_24_hours(self):
        """12-24h => poll every 60 min (3600s)."""
        self.assertEqual(polling_interval_seconds(18), 3600)

    def test_2_to_12_hours(self):
        """2-12h => poll every 30 min (1800s)."""
        self.assertEqual(polling_interval_seconds(6), 1800)

    def test_under_2_hours(self):
        """< 2h => poll every 5 min (300s)."""
        self.assertEqual(polling_interval_seconds(1), 300)

    def test_boundary_24h(self):
        """Exactly 24h => should be in the 12-24h band."""
        self.assertEqual(polling_interval_seconds(24), 3600)

    def test_boundary_12h(self):
        """Exactly 12h => should be in the 2-12h band."""
        self.assertEqual(polling_interval_seconds(12), 1800)

    def test_boundary_2h(self):
        """Exactly 2h => should be in the aggressive band (5 min)."""
        self.assertEqual(polling_interval_seconds(2), 300)


class TestHoursUntilNextGame(unittest.TestCase):
    def test_no_events(self):
        """Empty schedule returns inf."""
        self.assertEqual(hours_until_next_game({}, "basketball_nba"), float("inf"))

    def test_with_future_event(self):
        """An event 5 hours from now should return ~5."""
        from datetime import timedelta
        future = (
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + timedelta(hours=5)
        ).isoformat()
        schedule = {"basketball_nba": [{"commence_time": future}]}
        hours = hours_until_next_game(schedule, "basketball_nba")
        self.assertAlmostEqual(hours, 5.0, delta=0.1)

    def test_should_poll_in_window(self):
        """A game 3 hours out should be polled (2-12h band)."""
        from datetime import timedelta
        future = (
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + timedelta(hours=3)
        ).isoformat()
        schedule = {"basketball_nba": [{"commence_time": future}]}
        should, interval, hours = should_poll_sport(schedule, "basketball_nba")
        self.assertTrue(should)
        self.assertEqual(interval, 1800)

    def test_should_not_poll_far_future(self):
        """A game 48 hours out should NOT be polled."""
        from datetime import timedelta
        future = (
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + timedelta(hours=48)
        ).isoformat()
        schedule = {"basketball_nba": [{"commence_time": future}]}
        should, interval, hours = should_poll_sport(schedule, "basketball_nba")
        self.assertFalse(should)
        self.assertIsNone(interval)


# ---------------------------------------------------------------------------
# 8. API Response Cache
# ---------------------------------------------------------------------------


class TestAPICache(unittest.TestCase):
    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmpfile.close()
        import hive_scanner
        self._orig_path = hive_scanner.API_CACHE_PATH
        hive_scanner.API_CACHE_PATH = self._tmpfile.name

    def tearDown(self):
        import hive_scanner
        hive_scanner.API_CACHE_PATH = self._orig_path
        os.unlink(self._tmpfile.name)

    def test_cache_miss_returns_none(self):
        result = _get_cached_odds("basketball_nba", "eu", max_age_seconds=300)
        self.assertIsNone(result)

    def test_cache_hit_returns_data(self):
        data = [{"id": "ev1", "bookmakers": []}]
        _store_cached_odds("basketball_nba", "eu", data)
        result = _get_cached_odds("basketball_nba", "eu", max_age_seconds=300)
        self.assertEqual(result, data)

    def test_expired_cache_returns_none(self):
        import hive_scanner
        data = [{"id": "ev1"}]
        _store_cached_odds("basketball_nba", "eu", data)
        # Manually backdate the timestamp
        cache = hive_scanner._load_json(hive_scanner.API_CACHE_PATH)
        cache["basketball_nba|eu"]["ts"] = time.time() - 600
        hive_scanner._save_json(hive_scanner.API_CACHE_PATH, cache)
        result = _get_cached_odds("basketball_nba", "eu", max_age_seconds=300)
        self.assertIsNone(result)

    def test_different_sports_independent(self):
        data_nba = [{"id": "nba1"}]
        data_mlb = [{"id": "mlb1"}]
        _store_cached_odds("basketball_nba", "eu", data_nba)
        _store_cached_odds("baseball_mlb", "eu", data_mlb)
        self.assertEqual(_get_cached_odds("basketball_nba", "eu", 300), data_nba)
        self.assertEqual(_get_cached_odds("baseball_mlb", "eu", 300), data_mlb)


# ---------------------------------------------------------------------------
# 9. Seen-Bets Dedup (seen_bets.json)
# ---------------------------------------------------------------------------


class TestSeenBetsDedup(unittest.TestCase):
    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self._tmpfile.close()
        import hive_scanner
        self._orig_path = hive_scanner.SEEN_BETS_PATH
        hive_scanner.SEEN_BETS_PATH = self._tmpfile.name

    def tearDown(self):
        import hive_scanner
        hive_scanner.SEEN_BETS_PATH = self._orig_path
        os.unlink(self._tmpfile.name)

    def test_first_alert_passes(self):
        alerts = [{
            "matchup": "A @ B", "market": "h2h", "selection": "A",
            "book_key": "fanduel", "offered_odds_am": "+150",
        }]
        result = filter_new_alerts(alerts)
        self.assertEqual(len(result), 1)

    def test_duplicate_filtered(self):
        alerts = [{
            "matchup": "A @ B", "market": "h2h", "selection": "A",
            "book_key": "fanduel", "offered_odds_am": "+150",
        }]
        filter_new_alerts(alerts)
        result = filter_new_alerts(alerts)
        self.assertEqual(len(result), 0)

    def test_different_alert_passes(self):
        a1 = [{
            "matchup": "A @ B", "market": "h2h", "selection": "A",
            "book_key": "fanduel", "offered_odds_am": "+150",
        }]
        a2 = [{
            "matchup": "C @ D", "market": "h2h", "selection": "C",
            "book_key": "draftkings", "offered_odds_am": "+120",
        }]
        filter_new_alerts(a1)
        result = filter_new_alerts(a2)
        self.assertEqual(len(result), 1)

    def test_same_game_different_odds_passes(self):
        """If odds change, treat it as a new alert."""
        a1 = [{
            "matchup": "A @ B", "market": "h2h", "selection": "A",
            "book_key": "fanduel", "offered_odds_am": "+150",
        }]
        a2 = [{
            "matchup": "A @ B", "market": "h2h", "selection": "A",
            "book_key": "fanduel", "offered_odds_am": "+160",
        }]
        filter_new_alerts(a1)
        result = filter_new_alerts(a2)
        self.assertEqual(len(result), 1)

    def test_hash_includes_odds(self):
        """Hash should differ when odds differ."""
        alert1 = {
            "matchup": "A @ B", "market": "h2h", "selection": "A",
            "book_key": "fanduel", "offered_odds_am": "+150",
        }
        alert2 = {
            "matchup": "A @ B", "market": "h2h", "selection": "A",
            "book_key": "fanduel", "offered_odds_am": "+160",
        }
        self.assertNotEqual(_seen_bet_hash(alert1), _seen_bet_hash(alert2))

    def test_prune_removes_old_entries(self):
        """Entries older than TTL should be pruned."""
        import hive_scanner
        old_ttl = hive_scanner.SEEN_BETS_TTL_MINUTES
        hive_scanner.SEEN_BETS_TTL_MINUTES = 1  # 1 minute TTL
        seen = {"old_hash": {"ts": time.time() - 120}}  # 2 min old
        pruned = _prune_seen_bets(seen)
        self.assertEqual(len(pruned), 0)
        hive_scanner.SEEN_BETS_TTL_MINUTES = old_ttl


# ---------------------------------------------------------------------------
# 10. Discord Embed Formatting
# ---------------------------------------------------------------------------


class TestDiscordEmbed(unittest.TestCase):
    def test_embed_structure(self):
        alert = {
            "sport": "basketball_nba",
            "matchup": "Celtics @ Lakers",
            "market": "h2h",
            "selection": "Celtics",
            "book": "FanDuel",
            "book_key": "fanduel",
            "offered_odds_dec": 2.10,
            "offered_odds_am": "+110",
            "sharp_odds_am": "+100",
            "true_prob": 0.50,
            "fair_value_am": "+100",
            "ev_pct": 0.05,
            "quarter_kelly_pct": 1.25,
            "commence_time": "2025-01-01T00:00:00Z",
        }
        payload = build_discord_embed(alert)
        self.assertIn("embeds", payload)
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "+EV Alert from The Hive")
        self.assertEqual(embed["color"], EMBED_COLOR)
        self.assertIn("BEE BAKED BETS", embed["footer"]["text"])
        self.assertIn("5.00%", embed["description"])  # EV
        self.assertIn("1.25%", embed["description"])  # Quarter-Kelly
        self.assertIn("FanDuel", embed["description"])
        self.assertIn("Celtics", embed["description"])

    def test_spread_market_display(self):
        """Spread alerts should display 'Spread' as market type."""
        alert = {
            "sport": "basketball_nba",
            "matchup": "Celtics @ Lakers",
            "market": "spreads",
            "selection": "Celtics -3.5",
            "book": "DraftKings",
            "book_key": "draftkings",
            "offered_odds_dec": 1.95,
            "offered_odds_am": "-105",
            "sharp_odds_am": "-110",
            "true_prob": 0.52,
            "fair_value_am": "-108",
            "ev_pct": 0.03,
            "quarter_kelly_pct": 0.8,
            "commence_time": "2025-01-01T00:00:00Z",
        }
        payload = build_discord_embed(alert)
        self.assertIn("Spread", payload["embeds"][0]["description"])

    def test_totals_market_display(self):
        """Total alerts should display 'Total' as market type."""
        alert = {
            "sport": "basketball_nba",
            "matchup": "Celtics @ Lakers",
            "market": "totals",
            "selection": "Over 215.5",
            "book": "BetMGM",
            "book_key": "betmgm",
            "offered_odds_dec": 1.95,
            "offered_odds_am": "-105",
            "sharp_odds_am": "-110",
            "true_prob": 0.52,
            "fair_value_am": "-108",
            "ev_pct": 0.03,
            "quarter_kelly_pct": 0.8,
            "commence_time": "2025-01-01T00:00:00Z",
        }
        payload = build_discord_embed(alert)
        self.assertIn("Total", payload["embeds"][0]["description"])


if __name__ == "__main__":
    unittest.main()
