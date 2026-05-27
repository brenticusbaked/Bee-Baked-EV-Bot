"""Tests for time-decay edge adjustments."""

from datetime import datetime, timedelta, timezone

from utils.time_decay import adjusted_threshold, compute_time_decay


class TestComputeTimeDecay:
    def test_no_commence_time(self):
        ctx = compute_time_decay(None)
        assert ctx.phase == "unknown"
        assert ctx.threshold_multiplier == 1.0

    def test_far_future_is_early_phase(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        ctx = compute_time_decay(future)
        assert ctx.phase == "early"
        assert ctx.threshold_multiplier == 1.0

    def test_mid_range_is_mid_phase(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        ctx = compute_time_decay(future)
        assert ctx.phase == "mid"
        assert ctx.threshold_multiplier > 1.0

    def test_close_to_start_is_close_phase(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        ctx = compute_time_decay(future)
        assert ctx.phase == "close"
        assert ctx.threshold_multiplier < 1.0

    def test_past_start_is_lockout(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        ctx = compute_time_decay(past)
        assert ctx.phase == "lockout"
        assert ctx.threshold_multiplier == 0.0

    def test_explicit_now(self):
        now = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        commence = "2025-06-01T15:00:00Z"
        ctx = compute_time_decay(commence, now=now)
        assert ctx.phase == "mid"
        assert ctx.hours_to_event == 3.0


class TestAdjustedThreshold:
    def test_lockout_returns_infinity(self):
        from utils.time_decay import TimeDecayContext
        ctx = TimeDecayContext(hours_to_event=-1.0, threshold_multiplier=0.0, phase="lockout")
        assert adjusted_threshold(0.01, ctx) == float("inf")

    def test_normal_multiplier(self):
        from utils.time_decay import TimeDecayContext
        ctx = TimeDecayContext(hours_to_event=4.0, threshold_multiplier=1.15, phase="mid")
        result = adjusted_threshold(0.01, ctx)
        assert abs(result - 0.0115) < 1e-8

    def test_close_lowers_threshold(self):
        from utils.time_decay import TimeDecayContext
        ctx = TimeDecayContext(hours_to_event=1.0, threshold_multiplier=0.85, phase="close")
        result = adjusted_threshold(0.02, ctx)
        assert result < 0.02
