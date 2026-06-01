"""Tests for the shared SGO player-prop engine and league configs."""

from bot_propodds_mlb import MLB_PROP_CONFIG
from bot_propodds_nba import NBA_PROP_CONFIG
from services.sgo_props import (
    _normalize_stat_name,
    _parse_prop_offer,
    resolve_target_stats,
)


class TestStatNormalization:
    def test_mlb_strikeout_aliases_map_to_strikeouts(self):
        for alias in ("strikeouts", "Ks", "SO", "pitcher_strikeouts", "pitching_strikeouts"):
            assert _normalize_stat_name(alias, MLB_PROP_CONFIG.stat_aliases) == "strikeouts"

    def test_mlb_batter_aliases(self):
        assert _normalize_stat_name("total_bases", MLB_PROP_CONFIG.stat_aliases) == "total_bases"
        assert _normalize_stat_name("HR", MLB_PROP_CONFIG.stat_aliases) == "home_runs"
        assert _normalize_stat_name("RBI", MLB_PROP_CONFIG.stat_aliases) == "rbis"

    def test_nba_aliases_still_work(self):
        assert _normalize_stat_name("pts", NBA_PROP_CONFIG.stat_aliases) == "points"
        assert _normalize_stat_name("3PM", NBA_PROP_CONFIG.stat_aliases) == "three_pointers"

    def test_unknown_alias_returns_none(self):
        assert _normalize_stat_name("touchdowns", MLB_PROP_CONFIG.stat_aliases) is None


class TestResolveTargetStats:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("MLB_PROP_STATS", raising=False)
        assert "strikeouts" in resolve_target_stats(MLB_PROP_CONFIG)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MLB_PROP_STATS", "strikeouts, hits")
        result = resolve_target_stats(MLB_PROP_CONFIG)
        assert result == frozenset({"strikeouts", "hits"})

    def test_env_override_falls_back_when_all_invalid(self, monkeypatch):
        monkeypatch.setenv("MLB_PROP_STATS", "not_a_stat")
        assert resolve_target_stats(MLB_PROP_CONFIG) == MLB_PROP_CONFIG.target_stats


class TestParsePropOffer:
    def test_parses_mlb_strikeout_offer(self):
        offer = _parse_prop_offer(
            "pitching_strikeouts-GERRIT_COLE-over",
            {
                "oddID": "pitching_strikeouts-GERRIT_COLE-over",
                "playerName": "Gerrit Cole",
                "side": "over",
                "line": "6.5",
                "bookmakerID": "Pinnacle",
                "price": -110,
            },
            MLB_PROP_CONFIG.target_stats,
            MLB_PROP_CONFIG.stat_aliases,
        )
        assert offer is not None
        assert offer["stat"] == "strikeouts"
        assert offer["player"] == "Gerrit Cole"
        assert offer["side"] == "over"
        assert offer["line"] == "6.5"
        assert offer["book"] == "pinnacle"

    def test_rejects_offer_outside_target_stats(self):
        offer = _parse_prop_offer(
            "passing_yards-SOMEONE-over",
            {
                "oddID": "passing_yards-SOMEONE-over",
                "playerName": "Someone",
                "side": "over",
                "line": "250.5",
                "bookmakerID": "fanduel",
                "price": -110,
            },
            MLB_PROP_CONFIG.target_stats,
            MLB_PROP_CONFIG.stat_aliases,
        )
        assert offer is None


class TestLeagueConfigs:
    def test_mlb_config_targets_strikeouts(self):
        assert MLB_PROP_CONFIG.league_id == "MLB"
        assert MLB_PROP_CONFIG.sport_key == "baseball_mlb"
        assert "strikeouts" in MLB_PROP_CONFIG.target_stats

    def test_nba_config_unchanged(self):
        assert NBA_PROP_CONFIG.league_id == "NBA"
        assert NBA_PROP_CONFIG.sport_key == "basketball_nba"
        assert "points" in NBA_PROP_CONFIG.target_stats
