"""Tests for stale line detection."""

from utils.stale_line import detect_stale_line, staleness_edge_bonus, StalenessSignal


class TestDetectStaleLine:
    def test_no_sharp_movement_not_stale(self):
        result = detect_stale_line(
            sharp_price=1.90,
            sharp_opening_price=1.90,
            soft_price=1.95,
        )
        assert not result.is_stale
        assert result.staleness_score == 0.0

    def test_sharp_moved_soft_static_is_stale(self):
        result = detect_stale_line(
            sharp_price=1.75,
            sharp_opening_price=1.90,
            soft_price=1.95,
            soft_opening_price=1.95,
            threshold=0.01,
        )
        assert result.sharp_implied_move > 0.01
        assert result.soft_implied_move < 0.001
        assert result.staleness_score > 0.5

    def test_both_moved_equally_not_stale(self):
        result = detect_stale_line(
            sharp_price=1.75,
            sharp_opening_price=1.90,
            soft_price=1.80,
            soft_opening_price=1.95,
        )
        assert result.staleness_score < 0.5

    def test_returns_staleness_signal(self):
        result = detect_stale_line(
            sharp_price=1.80,
            sharp_opening_price=1.90,
            soft_price=1.95,
        )
        assert isinstance(result, StalenessSignal)


class TestStalenessEdgeBonus:
    def test_none_returns_zero(self):
        assert staleness_edge_bonus(None) == 0.0

    def test_not_stale_returns_zero(self):
        signal = StalenessSignal(
            sharp_implied_move=0.01,
            soft_implied_move=0.01,
            staleness_score=0.2,
            is_stale=False,
        )
        assert staleness_edge_bonus(signal) == 0.0

    def test_stale_returns_positive_bonus(self):
        signal = StalenessSignal(
            sharp_implied_move=0.05,
            soft_implied_move=0.001,
            staleness_score=0.8,
            is_stale=True,
        )
        bonus = staleness_edge_bonus(signal)
        assert bonus > 0.0
