"""Tests for simultaneous Kelly portfolio sizing."""

from utils.portfolio_kelly import single_kelly, simultaneous_kelly_units


class TestSingleKelly:
    def test_no_edge_returns_zero(self):
        assert single_kelly(0.0, 2.0) == 0.0

    def test_negative_edge_returns_zero(self):
        assert single_kelly(-0.05, 2.0) == 0.0

    def test_positive_edge(self):
        result = single_kelly(0.05, 2.0)
        assert result > 0.0

    def test_higher_edge_more_kelly(self):
        low = single_kelly(0.02, 2.0)
        high = single_kelly(0.08, 2.0)
        assert high > low

    def test_bad_odds_returns_zero(self):
        assert single_kelly(0.05, 1.0) == 0.0
        assert single_kelly(0.05, 0.5) == 0.0


class TestSimultaneousKellyUnits:
    def test_empty_returns_zeros(self):
        result = simultaneous_kelly_units([], [], 100.0)
        assert result == []

    def test_single_bet(self):
        result = simultaneous_kelly_units([0.05], [2.0], 100.0)
        assert len(result) == 1
        assert result[0] > 0.0

    def test_multiple_bets_smaller_than_single(self):
        single = simultaneous_kelly_units([0.05], [2.0], 100.0)
        multi = simultaneous_kelly_units(
            [0.05, 0.05, 0.05],
            [2.0, 2.0, 2.0],
            100.0,
        )
        assert all(u > 0 for u in multi)
        assert multi[0] < single[0]

    def test_respects_max_total_exposure(self):
        result = simultaneous_kelly_units(
            [0.10] * 5,
            [2.0] * 5,
            100.0,
            max_total=5.0,
        )
        assert sum(result) <= 5.0 + 1e-6

    def test_existing_exposure_reduces_room(self):
        without = simultaneous_kelly_units(
            [0.05], [2.0], 100.0, existing_exposure=0.0, max_total=10.0,
        )
        with_existing = simultaneous_kelly_units(
            [0.05], [2.0], 100.0, existing_exposure=9.0, max_total=10.0,
        )
        assert with_existing[0] <= without[0]

    def test_zero_bankroll_returns_zeros(self):
        result = simultaneous_kelly_units([0.05], [2.0], 0.0)
        assert result == [0.0]

    def test_no_edge_bets_return_zero(self):
        result = simultaneous_kelly_units([0.0, -0.01], [2.0, 2.0], 100.0)
        assert all(u == 0.0 for u in result)
