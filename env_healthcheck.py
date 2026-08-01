import argparse
import os
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class EnvVar:
    name: str
    required_for: str
    optional: bool = False
    expected_prefix: Optional[str] = None


CORE_VARS = [
    EnvVar("ODDS_API_KEY", "scheduled odds fetcher"),
    EnvVar("SUPABASE_URL", "Supabase persistence", expected_prefix="https://"),
    EnvVar("SUPABASE_KEY", "Supabase persistence"),
    EnvVar("DISCORD_WEBHOOK_URL", "default Discord alerts", expected_prefix="https://discord.com/api/webhooks/"),
]

LIVE_PUSH_VARS = [
    EnvVar("ODDS_PUSH_WS_URL", "live WebSocket odds feed", expected_prefix="wss://"),
    EnvVar("ODDS_PUSH_API_KEY", "live WebSocket odds feed authentication"),
    EnvVar("DISCORD_LIVE_HAMMER_WEBHOOK_URL", "LIVE HAMMER Discord lane", expected_prefix="https://discord.com/api/webhooks/"),
    EnvVar("DISCORD_WATCHLIST_WEBHOOK_URL", "WATCHLIST Discord lane", expected_prefix="https://discord.com/api/webhooks/"),
]

OPTIONAL_VARS = [
    EnvVar("ODDS_API_KEY_2", "secondary (500-credit reserve) odds key", optional=True),
    EnvVar("ODDS_API_KEY_3", "tertiary (500-credit reserve) odds key", optional=True),
    EnvVar("ODDS_API_KEY_4", "quaternary (500-credit reserve) odds key", optional=True),
    EnvVar("SGO_API_KEY", "SportsGameOdds props/grading", optional=True),
    EnvVar("SGO_API_KEY_2", "SportsGameOdds reserve props/grading key", optional=True),
    EnvVar("SGO_API_KEY_3", "SportsGameOdds reserve props/grading key", optional=True),
    EnvVar("SGO_GRADER_MAX_FETCHES", "SportsGameOdds grader fetch budget", optional=True),
    EnvVar("SGO_GRADER_FETCH_DELAY_SECONDS", "SportsGameOdds grader pacing delay", optional=True),
    EnvVar("SGO_LEAGUE_STAGGER_SECONDS", "SportsGameOdds league stagger delay", optional=True),
    EnvVar("ENABLE_WNBA_PROP_BOT", "WNBA prop-bot opt-in flag", optional=True),
    EnvVar("DISCORD_BET_ALERTS_WEBHOOK_URL", "dedicated bet-alert Discord lane", optional=True, expected_prefix="https://discord.com/api/webhooks/"),
    EnvVar("DISCORD_ARBITRAGE_WEBHOOK_URL", "dedicated arbitrage Discord lane", optional=True, expected_prefix="https://discord.com/api/webhooks/"),
    EnvVar("DISCORD_RESULTS_WEBHOOK_URL", "dedicated results/CLV Discord lane", optional=True, expected_prefix="https://discord.com/api/webhooks/"),
    EnvVar("DISCORD_STATUS_WEBHOOK_URL", "status Discord alerts", optional=True, expected_prefix="https://discord.com/api/webhooks/"),
    EnvVar("DISCORD_INJURY_WEBHOOK_URL", "injury/news Discord alerts", optional=True, expected_prefix="https://discord.com/api/webhooks/"),
    EnvVar("ODDS_PUSH_PROVIDER", "live feed provider label", optional=True),
    EnvVar("ODDS_PUSH_AUTH_MODE", "live feed authentication mode", optional=True),
    EnvVar("ODDS_PUSH_AUTH_HEADER", "live feed authentication header", optional=True),
    EnvVar("ODDS_PUSH_API_KEY_PARAM", "live feed query-string API key parameter", optional=True),
    EnvVar("ODDS_PUSH_SPORT", "default live feed sport", optional=True),
    EnvVar("ODDS_PUSH_SUBSCRIBE_JSON", "live feed subscription payload", optional=True),
    EnvVar("LIVE_HAMMER_EDGE_THRESHOLD", "LIVE HAMMER edge threshold", optional=True),
    EnvVar("WATCHLIST_EDGE_THRESHOLD", "WATCHLIST edge threshold", optional=True),
    EnvVar("LIVE_STALE_MIN_SCORE", "LIVE HAMMER stale-line score threshold", optional=True),
    EnvVar("LIVE_SHARP_BOOKS", "sharp books for live stale-line detection", optional=True),
    EnvVar("LIVE_SOFT_BOOKS", "soft books for live stale-line detection", optional=True),
]


def mask_value(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)"


def check_vars(vars_to_check: Iterable[EnvVar]) -> tuple[list[str], list[str]]:
    failures = []
    warnings = []
    for item in vars_to_check:
        value = os.getenv(item.name, "").strip()
        if not value:
            status = "OPTIONAL" if item.optional else "MISSING"
            print(f"[{status}] {item.name} - {item.required_for}")
            if not item.optional:
                failures.append(item.name)
            continue

        prefix_note = ""
        if item.expected_prefix and not value.startswith(item.expected_prefix):
            prefix_note = f" | warning: expected prefix {item.expected_prefix}"
            warnings.append(item.name)
        print(f"[OK] {item.name} = {mask_value(value)} - {item.required_for}{prefix_note}")
    return failures, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Redacted Bee-Baked environment variable healthcheck.")
    parser.add_argument("--live", action="store_true", help="Require live WebSocket and lane-specific Discord variables.")
    parser.add_argument("--optional", action="store_true", help="Show optional variables too.")
    args = parser.parse_args()

    print("Bee-Baked EV Bot environment healthcheck")
    print()
    print("Core")
    core_failures, core_warnings = check_vars(CORE_VARS)

    live_failures = []
    live_warnings = []
    if args.live:
        print()
        print("Live Push")
        live_failures, live_warnings = check_vars(LIVE_PUSH_VARS)

    if args.optional:
        print()
        print("Optional")
        _, optional_warnings = check_vars(OPTIONAL_VARS)
        live_warnings.extend(optional_warnings)

    failures = core_failures + live_failures
    warnings = core_warnings + live_warnings
    print()
    if failures:
        print(f"FAILED: {len(failures)} required variable(s) missing: {', '.join(failures)}")
        return 1
    if warnings:
        print(f"OK with warning(s): {', '.join(warnings)}")
        return 0
    print("OK: required environment variables are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
