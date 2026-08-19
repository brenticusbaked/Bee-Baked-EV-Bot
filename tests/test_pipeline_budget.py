"""Runtime-budget guards: event-scoped market pulls, per-key credit budgets,
lazy Statcast profiles and bounded HTTP backoff. All network access is mocked.
"""

import unittest
from unittest.mock import MagicMock, patch

import master_odds_fetcher as fetcher
import scraper_mlb_statcast_hr as hr_model
from services.http_client import RETRY_BACKOFF_MAX_SECONDS, build_session


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.headers = {}
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
    def test_each_api_key_gets_its_own_budget(self):
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
                ODDS_API_KEY_4="k4",
                ODDS_MAX_CREDITS_PER_RUN=50,
                ENABLE_MLB_F5_PULL=True,
                ENABLE_MLB_NRFI_PULL=True,
            ),
            patch.object(fetcher, "_fetch_config", side_effect=fake_fetch),
            patch.object(fetcher, "_fetch_event_scoped_config", return_value=1) as scoped,
            patch.object(fetcher, "get_master_cache", return_value={}),
            patch.object(fetcher, "save_master_cache"),
            patch.object(fetcher, "filter_config_in_season", side_effect=lambda cfg: cfg),
        ):
            fetcher.run_fetcher()

        # Distinct keys never share a tracker instance...
        trackers = {key: values[0] for key, values in seen.items()}
        self.assertEqual(len({id(tracker) for tracker in trackers.values()}), len(trackers))
        # ...and the same key reuses one, so its spend accumulates.
        self.assertIs(seen["k1"][0], seen["k1"][1])
        # The F5/NRFI key still has its full budget after the earlier tiers spent theirs.
        f5_tracker = scoped.call_args_list[0].args[6]
        self.assertEqual(f5_tracker.remaining, 50)


class ExchangeRegionPullTests(unittest.TestCase):
    """The exchanges (Novig/ProphetX/BetOpenly) are a separate Odds API region, so
    they cost an extra pull and are gated behind their own flag."""

    def _run(self, enabled):
        calls = []

        def fake_fetch(cache, api_key, config, label, region, books, props, tracker, max_events, **kwargs):
            calls.append({"label": label, "region": region, "books": books, **kwargs})
            return 1

        with (
            patch.multiple(
                fetcher,
                ODDS_API_KEY="k1",
                ODDS_API_KEY_2=None,
                ODDS_API_KEY_3=None,
                ODDS_API_KEY_4=None,
                ENABLE_ODDS_EXCHANGE_PULL=enabled,
                ENABLE_MLB_F5_PULL=False,
                ENABLE_MLB_NRFI_PULL=False,
            ),
            patch.object(fetcher, "_fetch_config", side_effect=fake_fetch),
            patch.object(fetcher, "get_master_cache", return_value={}),
            patch.object(fetcher, "save_master_cache"),
            patch.object(fetcher, "filter_config_in_season", side_effect=lambda cfg: cfg),
        ):
            result = fetcher.run_fetcher()
        return calls, result

    def test_exchange_pull_is_off_by_default(self):
        self.assertFalse(fetcher.ENABLE_ODDS_EXCHANGE_PULL)
        calls, _ = self._run(enabled=False)
        self.assertEqual([call["region"] for call in calls], [fetcher.SHARP_REGION, fetcher.SOFT_REGION])

    def test_enabled_exchange_pull_asks_the_exchange_region_for_main_markets_only(self):
        calls, result = self._run(enabled=True)
        exchange_calls = [call for call in calls if call["region"] == fetcher.EXCHANGE_REGION]
        self.assertEqual(len(exchange_calls), 1)
        self.assertEqual(exchange_calls[0]["books"], fetcher.EXCHANGE_BOOKS)
        # Props would be billed per event for feeds the exchanges barely list.
        self.assertFalse(exchange_calls[0]["fetch_props"])
        self.assertIn("exchange: 1/", result["detail"])

    def test_exchange_prices_stay_out_of_the_sharp_baseline(self):
        self.assertNotIn("pinnacle", fetcher.EXCHANGE_BOOKS)
        self.assertNotIn("novig", fetcher.SHARP_BOOKS)
        self.assertNotIn("novig", fetcher.SOFT_BOOKS)

    def test_props_are_still_fetched_for_the_retail_region(self):
        captured = {}

        def fake_fetch(cache, api_key, config, label, region, books, props, tracker, max_events, **kwargs):
            captured[region] = kwargs.get("fetch_props", True)
            return 1

        with (
            patch.multiple(fetcher, ODDS_API_KEY="k1", ODDS_API_KEY_2=None, ODDS_API_KEY_3=None, ODDS_API_KEY_4=None),
            patch.object(fetcher, "_fetch_config", side_effect=fake_fetch),
            patch.object(fetcher, "get_master_cache", return_value={}),
            patch.object(fetcher, "save_master_cache"),
            patch.object(fetcher, "filter_config_in_season", side_effect=lambda cfg: cfg),
        ):
            fetcher.run_fetcher()

        self.assertTrue(captured[fetcher.SOFT_REGION])


class RetailBookCoverageTests(unittest.TestCase):
    def test_fanatics_rides_along_in_the_existing_retail_region(self):
        # Credits are charged per market per region, so an extra bookmaker in the
        # us region is free.
        self.assertIn("fanatics", fetcher.DEFAULT_SOFT_BOOKS)
        self.assertEqual(fetcher.SOFT_REGION, "us")


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
