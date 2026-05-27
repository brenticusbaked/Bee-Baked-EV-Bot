"""Correlated exposure management for syndicate bankroll protection.

In a professional syndicate, betting the same-game spread *and* total
creates correlated risk that naive per-bet Kelly sizing ignores. This
module tracks exposure by event and applies correlation penalties to
prevent bankroll concentration in a single outcome cluster.

Correlation groups:
    - Same event, same market  -> full correlation (1.0)
    - Same event, different market -> partial correlation (configurable)
    - Same team, different event  -> weak correlation (configurable)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from utils.thresholds import env_float, env_int


SAME_EVENT_CROSS_MARKET_CORR = env_float("CORR_SAME_EVENT_CROSS_MARKET", 0.35)
SAME_TEAM_CORR = env_float("CORR_SAME_TEAM", 0.10)
MAX_EVENT_EXPOSURE_UNITS = env_float("MAX_EVENT_EXPOSURE_UNITS", 8.0)
MAX_CORRELATED_EXPOSURE_UNITS = env_float("MAX_CORRELATED_EXPOSURE_UNITS", 15.0)
MAX_CONCURRENT_SAME_EVENT = env_int("MAX_CONCURRENT_SAME_EVENT", 4)


@dataclass(frozen=True)
class CorrelationKey:
    event_id: str
    market_type: str
    side: str


@dataclass
class ExposureEntry:
    event_id: str
    market_type: str
    side: str
    matchup: str
    units: float
    edge: float
    teams: Tuple[str, str] = ("", "")


@dataclass
class ExposureTracker:
    """Tracks live exposure across the current session's bet slate."""

    entries: List[ExposureEntry] = field(default_factory=list)
    _by_event: Dict[str, List[ExposureEntry]] = field(default_factory=lambda: defaultdict(list))
    _by_team: Dict[str, List[ExposureEntry]] = field(default_factory=lambda: defaultdict(list))

    def add(self, entry: ExposureEntry) -> None:
        self.entries.append(entry)
        self._by_event[entry.event_id].append(entry)
        for team in entry.teams:
            if team:
                self._by_team[team.lower().strip()].append(entry)

    def event_exposure(self, event_id: str) -> float:
        return sum(e.units for e in self._by_event.get(event_id, []))

    def event_bet_count(self, event_id: str) -> int:
        return len(self._by_event.get(event_id, []))

    def team_exposure(self, team: str) -> float:
        return sum(e.units for e in self._by_team.get(team.lower().strip(), []))

    def correlated_exposure(self, event_id: str, market_type: str, teams: Tuple[str, str]) -> float:
        """Weighted sum of correlated exposure for a proposed new bet."""
        total = 0.0
        for entry in self._by_event.get(event_id, []):
            if entry.market_type == market_type:
                total += entry.units * 1.0
            else:
                total += entry.units * SAME_EVENT_CROSS_MARKET_CORR

        for team in teams:
            if not team:
                continue
            key = team.lower().strip()
            for entry in self._by_team.get(key, []):
                if entry.event_id == event_id:
                    continue
                total += entry.units * SAME_TEAM_CORR

        return total


@dataclass(frozen=True)
class ExposureDecision:
    allowed: bool
    adjusted_units: float
    reason: str
    correlated_exposure: float


def check_exposure(
    tracker: ExposureTracker,
    event_id: str,
    market_type: str,
    proposed_units: float,
    teams: Tuple[str, str] = ("", ""),
    max_event: float = 0.0,
    max_correlated: float = 0.0,
    max_same_event_bets: int = 0,
) -> ExposureDecision:
    """Check whether a proposed bet fits within correlation-aware exposure limits."""
    max_evt = max_event if max_event > 0 else MAX_EVENT_EXPOSURE_UNITS
    max_corr = max_correlated if max_correlated > 0 else MAX_CORRELATED_EXPOSURE_UNITS
    max_bets = max_same_event_bets if max_same_event_bets > 0 else MAX_CONCURRENT_SAME_EVENT

    current_event = tracker.event_exposure(event_id)
    current_correlated = tracker.correlated_exposure(event_id, market_type, teams)
    event_bet_count = tracker.event_bet_count(event_id)

    if event_bet_count >= max_bets:
        return ExposureDecision(
            allowed=False,
            adjusted_units=0.0,
            reason=f"max {max_bets} bets per event reached",
            correlated_exposure=current_correlated,
        )

    event_remaining = max(0.0, max_evt - current_event)
    corr_remaining = max(0.0, max_corr - current_correlated)

    if event_remaining <= 0:
        return ExposureDecision(
            allowed=False,
            adjusted_units=0.0,
            reason=f"event exposure at cap ({max_evt}u)",
            correlated_exposure=current_correlated,
        )

    adjusted = min(proposed_units, event_remaining, corr_remaining)
    if adjusted <= 0:
        return ExposureDecision(
            allowed=False,
            adjusted_units=0.0,
            reason=f"correlated exposure at cap ({max_corr}u)",
            correlated_exposure=current_correlated,
        )

    reason = "ok"
    if adjusted < proposed_units:
        reason = f"reduced from {proposed_units:.2f}u to {adjusted:.2f}u (exposure limits)"

    return ExposureDecision(
        allowed=True,
        adjusted_units=round(adjusted, 4),
        reason=reason,
        correlated_exposure=round(current_correlated, 4),
    )
