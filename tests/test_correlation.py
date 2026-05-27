"""Tests for correlated exposure management."""

from utils.correlation import (
    ExposureEntry,
    ExposureTracker,
    check_exposure,
)


def _make_entry(event_id="evt1", market="spreads", units=2.0, teams=("Lakers", "Bulls")):
    return ExposureEntry(
        event_id=event_id,
        market_type=market,
        side="Lakers -3.5",
        matchup="Lakers @ Bulls",
        units=units,
        edge=0.04,
        teams=teams,
    )


class TestExposureTracker:
    def test_empty_tracker(self):
        tracker = ExposureTracker()
        assert tracker.event_exposure("evt1") == 0.0
        assert tracker.event_bet_count("evt1") == 0

    def test_add_updates_exposure(self):
        tracker = ExposureTracker()
        tracker.add(_make_entry(units=3.0))
        assert tracker.event_exposure("evt1") == 3.0
        assert tracker.event_bet_count("evt1") == 1

    def test_multiple_entries_same_event(self):
        tracker = ExposureTracker()
        tracker.add(_make_entry(units=2.0, market="spreads"))
        tracker.add(_make_entry(units=1.5, market="totals"))
        assert tracker.event_exposure("evt1") == 3.5
        assert tracker.event_bet_count("evt1") == 2

    def test_team_exposure(self):
        tracker = ExposureTracker()
        tracker.add(_make_entry(units=2.0, teams=("Lakers", "Bulls")))
        assert tracker.team_exposure("Lakers") == 2.0
        assert tracker.team_exposure("Celtics") == 0.0


class TestCheckExposure:
    def test_first_bet_allowed(self):
        tracker = ExposureTracker()
        decision = check_exposure(tracker, "evt1", "spreads", 3.0, ("A", "B"))
        assert decision.allowed
        assert decision.adjusted_units == 3.0

    def test_event_cap_reduces_units(self):
        tracker = ExposureTracker()
        tracker.add(_make_entry(units=6.0))
        decision = check_exposure(
            tracker, "evt1", "totals", 5.0, ("Lakers", "Bulls"),
            max_event=8.0,
        )
        assert decision.allowed
        assert decision.adjusted_units == 2.0

    def test_event_cap_blocks(self):
        tracker = ExposureTracker()
        tracker.add(_make_entry(units=8.0))
        decision = check_exposure(
            tracker, "evt1", "totals", 1.0, ("Lakers", "Bulls"),
            max_event=8.0,
        )
        assert not decision.allowed

    def test_max_bets_per_event(self):
        tracker = ExposureTracker()
        for i in range(4):
            tracker.add(_make_entry(units=1.0, market=f"m{i}"))
        decision = check_exposure(
            tracker, "evt1", "totals", 1.0, ("Lakers", "Bulls"),
            max_same_event_bets=4,
        )
        assert not decision.allowed

    def test_different_events_independent(self):
        tracker = ExposureTracker()
        tracker.add(_make_entry(event_id="evt1", units=7.0))
        decision = check_exposure(
            tracker, "evt2", "spreads", 5.0, ("Celtics", "Heat"),
            max_event=8.0,
        )
        assert decision.allowed
        assert decision.adjusted_units == 5.0
