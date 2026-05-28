"""Tests for sport season calendar."""

from datetime import date

from utils.seasons import filter_config_in_season, filter_in_season, is_sport_in_season


class TestIsInSeason:
    def test_nba_in_season_january(self):
        assert is_sport_in_season("basketball_nba", date(2026, 1, 15)) is True

    def test_nba_off_season_august(self):
        assert is_sport_in_season("basketball_nba", date(2026, 8, 1)) is False

    def test_nba_in_season_playoffs_june(self):
        assert is_sport_in_season("basketball_nba", date(2026, 6, 15)) is True

    def test_nhl_off_season_july(self):
        assert is_sport_in_season("icehockey_nhl", date(2026, 7, 15)) is False

    def test_nhl_in_season_march(self):
        assert is_sport_in_season("icehockey_nhl", date(2026, 3, 1)) is True

    def test_mlb_in_season_july(self):
        assert is_sport_in_season("baseball_mlb", date(2026, 7, 4)) is True

    def test_mlb_off_season_december(self):
        assert is_sport_in_season("baseball_mlb", date(2026, 12, 25)) is False

    def test_wnba_in_season_june(self):
        assert is_sport_in_season("basketball_wnba", date(2026, 6, 1)) is True

    def test_wnba_off_season_february(self):
        assert is_sport_in_season("basketball_wnba", date(2026, 2, 1)) is False

    def test_nfl_in_season_october(self):
        assert is_sport_in_season("americanfootball_nfl", date(2026, 10, 15)) is True

    def test_nfl_in_season_january_playoffs(self):
        assert is_sport_in_season("americanfootball_nfl", date(2026, 1, 10)) is True

    def test_nfl_off_season_april(self):
        assert is_sport_in_season("americanfootball_nfl", date(2026, 4, 1)) is False

    def test_unknown_sport_always_in_season(self):
        assert is_sport_in_season("mma_ufc", date(2026, 6, 1)) is True


class TestFilterInSeason:
    def test_filters_off_season(self):
        sports = ["basketball_nba", "baseball_mlb", "icehockey_nhl"]
        result = filter_in_season(sports, date(2026, 8, 1))
        assert "baseball_mlb" in result
        assert "basketball_nba" not in result
        assert "icehockey_nhl" not in result

    def test_all_in_season(self):
        sports = ["basketball_nba", "baseball_mlb"]
        result = filter_in_season(sports, date(2026, 5, 15))
        assert len(result) == 2


class TestFilterConfigInSeason:
    def test_filters_config_entries(self):
        config = {
            "basketball_nba": "spreads",
            "baseball_mlb": "h2h",
            "icehockey_nhl": "spreads",
        }
        result = filter_config_in_season(config, date(2026, 8, 1))
        assert "baseball_mlb" in result
        assert "basketball_nba" not in result
        assert "icehockey_nhl" not in result

    def test_empty_config_returns_empty(self):
        assert filter_config_in_season({}, date(2026, 5, 1)) == {}
