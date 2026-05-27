"""Tests for market efficiency scoring."""

from utils.market_efficiency import score_market_efficiency, MarketEfficiency


class TestScoreMarketEfficiency:
    def test_tight_market_scores_high(self):
        sharp = [1.90, 1.90]
        soft = [1.92, 1.88, 1.91, 1.89]
        result = score_market_efficiency(sharp, soft, edge=0.03)
        assert isinstance(result, MarketEfficiency)
        assert result.score > 0.5

    def test_wide_vig_scores_low(self):
        sharp = [1.60, 2.50]
        soft = [1.55]
        result = score_market_efficiency(sharp, soft, edge=0.02)
        assert result.score < 0.5

    def test_many_books_scores_higher(self):
        sharp = [1.90, 1.90]
        few_soft = [1.92]
        many_soft = [1.92, 1.91, 1.93, 1.90, 1.92]
        few_result = score_market_efficiency(sharp, few_soft, edge=0.03)
        many_result = score_market_efficiency(sharp, many_soft, edge=0.03)
        assert many_result.score >= few_result.score

    def test_zero_edge(self):
        sharp = [1.90, 1.90]
        soft = [1.90, 1.90]
        result = score_market_efficiency(sharp, soft, edge=0.0)
        assert result.score >= 0.0
        assert result.edge_to_vig_ratio == 0.0

    def test_overround_calculated(self):
        sharp = [1.90, 1.90]
        result = score_market_efficiency(sharp, [1.90], edge=0.03)
        assert result.overround > 0.0

    def test_single_sharp_returns_zero_overround(self):
        result = score_market_efficiency([1.90], [1.92], edge=0.03)
        assert result.overround == 0.0
