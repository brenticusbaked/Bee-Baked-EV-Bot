"""Runtime-budget guards: event-scoped market pulls, per-key credit budgets,
lazy Statcast profiles and bounded HTTP backoff. All network access is mocked.
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import master_odds_fetcher as fetcher
import scraper_mlb_statcast_hr as hr_model
from services.http_client import RETRY_BACKOFF_MAX_SECONDS, build_session


def _response(payload, remaining="60"):
    response = MagicMock()
    response.json.return_value = payload
    response.headers = {"x-requests-remaining": remaining}
    return response


def _cached_event(event_id, home="Home Team", away="Away Team"):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [{"name": home, "price": 1.9}]}]}
        ],
    }


class EventScopedPullTests(unittest.TestCase):
    def test_uses_per_event_endpoint_and_merges_markets(self):
        cache = {"baseball_mlb": [_cached_event("evt-1")]}
        payload = {
            "id": "evt-1",
            "bookmakers": [
                {
                    "key": "pinnacle",
                    "markets": [
                        {"key": "h2h_1st_5_innings", "outcomes": [{"name": "Home Team", "price": 2.05}]}
                    ],
                }
            ],
        }
        tracker = fetcher._CreditTracker(100)

        with (
            patch.object(fetcher, "request", return_value=_response(payload)) as mock_request,
            patch.object(fetcher.time, "sleep"),
        ):
            count = fetcher._fetch_event_scoped_config(
                cache,
                "key",
                {"baseball_mlb": "h2h_1st_5_innings,totals_1st_5_innings"},
                "mlb f5 sharp eu",
                "eu",
                "pinnacle",
                tracker,
                50,
            )

        self.assertEqual(count, 1)
        url = mock_request.call_args.args[1]
        self.assertIn("/events/evt-1/odds", url)
        markets = [
            market["key"]
            for book in cache["baseball_mlb"][0]["bookmakers"]
            for market in book["markets"]
        ]
        # The pre-existing full-game market survives the merge.
        self.assertIn("h2h", markets)
        self.assertIn("h2h_1st_5_innings", markets)

    def test_skips_when_no_cached_events(self):
        tracker = fetcher._CreditTracker(100)
        with patch.object(fetcher, "request") as mock_request:
            count = fetcher._fetch_event_scoped_config(
                {}, "key", {"baseball_mlb": "runs_1st_inning"}, "nrfi", "eu", "pinnacle", tracker, 50
            )
        self.assertEqual(count, 0)
        mock_request.assert_not_called()
        self.assertEqual(tracker.used, 0)

    def test_stops_when_credit_budget_is_exhausted(self):
        cache = {"baseball_mlb": [_cached_event(f"evt-{i}") for i in range(5)]}
        # 2 markets => 4 credits per event, so only two events fit in 10.
        tracker = fetcher._CreditTracker(10)
        payload = {"id": "evt-0", "bookmakers": []}

        with (
            patch.object(fetcher, "request", return_value=_response(payload)) as mock_request,
            patch.object(fetcher.time, "sleep"),
        ):
            fetcher._fetch_event_scoped_config(
                cache,
                "key",
                {"baseball_mlb": "h2h_1st_5_innings,totals_1st_5_innings"},
                "mlb f5 sharp eu",
                "eu",
                "pinnacle",
                tracker,
                50,
            )

        self.assertEqual(mock_request.call_count, 2)
        self.assertLessEqual(tracker.used, tracker.limit)

    def test_max_events_caps_requests(self):
        cache = {"baseball_mlb": [_cached_event(f"evt-{i}") for i in range(9)]}
        tracker = fetcher._CreditTracker(1000)
        with (
            patch.object(fetcher, "request", return_value=_response({"bookmakers": []})) as mock_request,
            patch.object(fetcher.time, "sleep"),
        ):
            fetcher._fetch_event_scoped_config(
                cache, "key", {"baseball_mlb": "runs_1st_inning"}, "nrfi", "eu", "pinnacle", tracker, 3
            )
        self.assertEqual(mock_request.call_count, 3)

    def test_one_failing_event_does_not_abort_the_pull(self):
        cache = {"baseball_mlb": [_cached_event("evt-1"), _cached_event("evt-2")]}
        tracker = fetcher._CreditTracker(100)
        responses = [RuntimeError("422"), _response({"bookmakers": []})]

        with (
            patch.object(fetcher, "request", side_effect=responses) as mock_request,
            patch.object(fetcher.time, "sleep"),
        ):
            count = fetcher._fetch_event_scoped_config(
                cache, "key", {"baseball_mlb": "runs_1st_inning"}, "nrfi", "eu", "pinnacle", tracker, 50
            )

        self.assertEqual(mock_request.call_count, 2)
        self.assertEqual(count, 1)


class PerKeyCreditBudgetTests(unittest.TestCase):
    def test_each_key_gets_its_own_cap_sized_to_its_plan(self):
        seen = {}

        def fake_fetch(cache, api_key, config, label, region, books, props, tracker, max_events):
            seen.setdefault(api_key, []).append(tracker)
            tracker.charge(tracker.limit)
            return 1

        with (
            patch.multiple(
                fetcher,
                ODDS_API_KEY="k1",
                ODDS_API_KEY_2="k2",
                ODDS_API_KEY_3="k3",
                ODDS_MAX_CREDITS_PER_RUN=700,
                ODDS_MAX_CREDITS_PER_RUN_SECONDARY=20,
                ENABLE_MLB_F5_PULL=False,
                ENABLE_MLB_NRFI_PULL=False,
            ),
            patch.object(fetcher, "_fetch_config", side_effect=fake_fetch),
            patch.object(fetcher, "get_master_cache", return_value={}),
            patch.object(fetcher, "save_master_cache"),
            patch.object(fetcher, "filter_config_in_season", side_effect=lambda cfg: cfg),
        ):
            fetcher.run_fetcher()

        # The same key reuses one tracker, so its spend accumulates across tiers...
        self.assertIs(seen["k1"][0], seen["k1"][1])
        # ...while a 500/month key can't be handed the primary key's ceiling.
        self.assertEqual(seen["k1"][0].limit, 700)
        self.assertEqual(seen["k2"][0].limit, 20)
        self.assertEqual(seen["k3"][0].limit, 20)
        self.assertIsNot(seen["k1"][0], seen["k2"][0])

    def test_quota_header_floors_the_run_budget(self):
        tracker = fetcher._CreditTracker(700, reserve=50)
        self.assertEqual(tracker.remaining, 700)

        tracker.observe(_response({}))  # 60 credits left on the key itself
        self.assertEqual(tracker.remaining, 10)
        self.assertFalse(tracker.charge(700))

    def test_zero_reserve_lets_a_key_run_dry(self):
        tracker = fetcher._CreditTracker(700, reserve=0)
        tracker.observe(_response({}))
        self.assertEqual(tracker.remaining, 60)

    def test_unparseable_quota_header_is_ignored(self):
        tracker = fetcher._CreditTracker(100, reserve=0)
        response = _response({})
        response.headers = {"x-requests-remaining": "n/a"}
        tracker.observe(response)
        self.assertEqual(tracker.remaining, 100)


class DailyPullClaimTests(unittest.TestCase):
    def test_claimed_once_per_day_after_the_cutoff_hour(self):
        state = {}

        with (
            patch.object(fetcher, "ODDS_DAILY_PULL_MIN_LOCAL_HOUR", 12),
            patch.object(fetcher, "get_local_date_str", return_value="2026-08-11"),
            patch.object(fetcher, "get_local_now", return_value=datetime(2026, 8, 11, 13, tzinfo=timezone.utc)),
            patch.object(fetcher, "load_tracker_state", side_effect=lambda key, fallback=None: dict(state)),
            patch.object(fetcher, "save_tracker_state", side_effect=lambda key, data: state.update(data)),
        ):
            self.assertTrue(fetcher._claim_daily_pull("mlb_f5"))
            # A later window in the same day must not pay for the pull again.
            self.assertFalse(fetcher._claim_daily_pull("mlb_f5"))
            # A different pull has its own claim.
            self.assertTrue(fetcher._claim_daily_pull("mlb_nrfi"))

    def test_not_claimed_before_the_cutoff_hour(self):
        with (
            patch.object(fetcher, "ODDS_DAILY_PULL_MIN_LOCAL_HOUR", 12),
            patch.object(fetcher, "get_local_now", return_value=datetime(2026, 8, 11, 8, tzinfo=timezone.utc)),
            patch.object(fetcher, "load_tracker_state") as load,
        ):
            self.assertFalse(fetcher._claim_daily_pull("mlb_f5"))
        load.assert_not_called()

    def test_yesterdays_claim_does_not_block_today(self):
        with (
            patch.object(fetcher, "ODDS_DAILY_PULL_MIN_LOCAL_HOUR", 12),
            patch.object(fetcher, "get_local_date_str", return_value="2026-08-11"),
            patch.object(fetcher, "get_local_now", return_value=datetime(2026, 8, 11, 13, tzinfo=timezone.utc)),
            patch.object(fetcher, "load_tracker_state", return_value={"mlb_f5": "2026-08-10"}),
            patch.object(fetcher, "save_tracker_state"),
        ):
            self.assertTrue(fetcher._claim_daily_pull("mlb_f5"))

    def test_event_scoped_pulls_are_skipped_when_the_day_is_claimed(self):
        with (
            patch.multiple(
                fetcher,
                ODDS_API_KEY="k1",
                ODDS_API_KEY_2=None,
                ODDS_API_KEY_3=None,
                ENABLE_MLB_F5_PULL=True,
                ENABLE_MLB_NRFI_PULL=True,
            ),
            patch.object(fetcher, "_claim_daily_pull", return_value=False),
            patch.object(fetcher, "_fetch_config", return_value=0),
            patch.object(fetcher, "_fetch_event_scoped_config") as scoped,
            patch.object(fetcher, "get_master_cache", return_value={}),
            patch.object(fetcher, "save_master_cache"),
            patch.object(fetcher, "filter_config_in_season", side_effect=lambda cfg: cfg),
        ):
            fetcher.run_fetcher()
        scoped.assert_not_called()

    def test_event_scoped_pulls_bill_the_primary_key(self):
        with (
            patch.multiple(
                fetcher,
                ODDS_API_KEY="k1",
                ODDS_API_KEY_2=None,
                ODDS_API_KEY_3=None,
                ODDS_MAX_CREDITS_PER_RUN=700,
                ENABLE_MLB_F5_PULL=True,
                ENABLE_MLB_NRFI_PULL=True,
            ),
            patch.object(fetcher, "_claim_daily_pull", return_value=True),
            patch.object(fetcher, "_fetch_config", return_value=0),
            patch.object(fetcher, "_fetch_event_scoped_config", return_value=1) as scoped,
            patch.object(fetcher, "get_master_cache", return_value={}),
            patch.object(fetcher, "save_master_cache"),
            patch.object(fetcher, "filter_config_in_season", side_effect=lambda cfg: cfg),
        ):
            fetcher.run_fetcher()

        keys = {call.args[1] for call in scoped.call_args_list}
        limits = {call.args[6].limit for call in scoped.call_args_list}
        # The 500/month keys can't fund a per-event pull across a 15-game slate.
        self.assertEqual(keys, {"k1"})
        self.assertEqual(limits, {700})


class LazyStatcastTests(unittest.TestCase):
    def _batter(self, player_id, name, team, iso, hr_per_ab):
        return {
            "name": name,
            "team": team,
            "home_runs": 30,
            "at_bats": 400,
            "hr_per_ab": hr_per_ab,
            "slg": 0.5,
            "iso": iso,
            "player_id": player_id,
            "season": 2026,
            "statcast": None,
        }

    def _slate(self):
        return {
            # Slate context keys are normalized lowercase team-name variants.
            "slate team": {
                "opponent": "Other Team",
                "venue": "Park",
                "park_factor": 1.0,
                "context_boost": 0.0,
                "weather": {},
            }
        }

    def test_profile_fetched_only_for_slate_and_tier_survivors(self):
        batters = {
            "1": self._batter("1", "Slate Slugger", "Slate Team", 0.260, 0.060),
            "2": self._batter("2", "Off Slate Slugger", "Dugout Crew", 0.260, 0.060),
            "3": self._batter("3", "Slate Contact Hitter", "Slate Team", 0.090, 0.005),
        }
        with patch.object(hr_model, "_fetch_statcast_profile", return_value={}) as profile:
            hr_model.calculate_hr_units(batters, self._slate())

        fetched = {call.args[1] for call in profile.call_args_list}
        self.assertEqual(fetched, {"Slate Slugger"})

    def test_profile_budget_is_capped(self):
        batters = {
            str(i): self._batter(str(i), f"Slugger {i}", "Slate Team", 0.260, 0.060)
            for i in range(6)
        }
        with (
            patch.object(hr_model, "HR_STATCAST_MAX_PROFILES", 2),
            patch.object(hr_model, "_fetch_statcast_profile", return_value={}) as profile,
        ):
            recommendations = hr_model.calculate_hr_units(batters, self._slate())

        self.assertEqual(profile.call_count, 2)
        # Batters past the cap are still priced, just without the Statcast bump.
        self.assertEqual(len(recommendations), 6)

    def test_statcast_bump_still_applied_when_profile_present(self):
        def batters():
            return {"1": self._batter("1", "Slate Slugger", "Slate Team", 0.260, 0.060)}

        with patch.object(hr_model, "_fetch_statcast_profile", return_value={}):
            baseline = hr_model.calculate_hr_units(batters(), self._slate())
        with patch.object(
            hr_model,
            "_fetch_statcast_profile",
            return_value={"launch_speed": 95.0, "launch_angle": 18.0, "barrel_rate": 0.15},
        ):
            boosted = hr_model.calculate_hr_units(batters(), self._slate())

        self.assertGreater(boosted[0]["implied_prob"], baseline[0]["implied_prob"])


class RetryBackoffTests(unittest.TestCase):
    def test_retry_after_header_cannot_stall_the_run(self):
        retry = build_session().get_adapter("https://api.example.com").max_retries
        self.assertFalse(retry.respect_retry_after_header)
        self.assertEqual(retry.backoff_max, RETRY_BACKOFF_MAX_SECONDS)

    def test_total_backoff_is_bounded(self):
        retry = build_session().get_adapter("https://api.example.com").max_retries
        worst_case = RETRY_BACKOFF_MAX_SECONDS * (retry.total or 0)
        self.assertLessEqual(worst_case, 60)


if __name__ == "__main__":
    unittest.main()
