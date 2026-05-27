"""Tests for Shin's vig removal method."""

from utils.shin import shin_probabilities, shin_fair_probabilities_from_prices


class TestShinProbabilities:
    def test_empty_returns_empty(self):
        assert shin_probabilities([]) == []

    def test_single_outcome_returns_one(self):
        assert shin_probabilities([0.6]) == [1.0]

    def test_fair_market_returns_unchanged(self):
        result = shin_probabilities([0.5, 0.5])
        assert abs(sum(result) - 1.0) < 1e-8

    def test_two_outcome_with_vig(self):
        implied = [0.55, 0.55]
        result = shin_probabilities(implied)
        assert abs(sum(result) - 1.0) < 1e-6
        assert all(0 < p < 1 for p in result)
        assert abs(result[0] - result[1]) < 1e-6

    def test_longshot_gets_more_vig_loaded(self):
        favourite = 0.75
        longshot = 0.35
        result = shin_probabilities([favourite, longshot])
        assert abs(sum(result) - 1.0) < 1e-6
        assert result[0] > result[1]
        mult_fair = [p / (favourite + longshot) for p in [favourite, longshot]]
        assert result[1] >= mult_fair[1] - 1e-4

    def test_three_outcome_market(self):
        implied = [0.40, 0.30, 0.40]
        result = shin_probabilities(implied)
        assert abs(sum(result) - 1.0) < 1e-6
        assert len(result) == 3

    def test_no_overround_returns_as_is(self):
        implied = [0.4, 0.6]
        result = shin_probabilities(implied)
        assert abs(result[0] - 0.4) < 1e-6
        assert abs(result[1] - 0.6) < 1e-6


class TestShinFairProbabilitiesFromPrices:
    def test_two_way_market(self):
        prices = {
            ("over", "7.5"): 1.90,
            ("under", "7.5"): 1.90,
        }
        result = shin_fair_probabilities_from_prices(prices)
        assert len(result) == 2
        total = sum(result.values())
        assert abs(total - 1.0) < 1e-6

    def test_preserves_keys(self):
        prices = {
            ("team a", ""): 2.10,
            ("team b", ""): 1.80,
        }
        result = shin_fair_probabilities_from_prices(prices)
        assert ("team a", "") in result
        assert ("team b", "") in result

    def test_asymmetric_vig_loading(self):
        prices = {
            ("favourite", ""): 1.40,
            ("underdog", ""): 3.20,
        }
        result = shin_fair_probabilities_from_prices(prices)
        assert result[("favourite", "")] > result[("underdog", "")]
        assert abs(sum(result.values()) - 1.0) < 1e-6
