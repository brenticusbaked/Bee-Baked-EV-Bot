"""Daily Monte Carlo risk simulation for bankroll trajectory and drawdown estimation."""

import os
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from db_manager import get_all_graded_bets
from services.http_client import post_discord
from utils.odds import profit_for_result
from utils.thresholds import env_float, env_int


DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")
MC_SIMULATIONS = max(100, env_int("MC_SIMULATIONS", 10000))
MC_HORIZON_BETS = max(10, env_int("MC_HORIZON_BETS", 200))
MC_BANKROLL_UNITS = env_float("MC_BANKROLL_UNITS", 100.0)
MC_RUIN_THRESHOLD = env_float("MC_RUIN_THRESHOLD", 0.0)
MC_LOOKBACK_DAYS = max(1, env_int("MC_LOOKBACK_DAYS", 30))
MC_MIN_GRADED_BETS = max(10, env_int("MC_MIN_GRADED_BETS", 25))


def _parse_datetime_utc(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{raw}T00:00:00+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _estimate_bet_profile(graded_bets: List[dict]) -> Dict[str, float]:
    """Extract win rate, average win payout, and average unit size from history."""
    if not graded_bets:
        return {"win_rate": 0.50, "avg_win_payout": 0.95, "avg_loss": 1.0, "avg_units": 1.0}

    wins = [b for b in graded_bets if str(b.get("result", "")).upper() == "WIN"]
    losses = [b for b in graded_bets if str(b.get("result", "")).upper() == "LOSS"]
    total = len(wins) + len(losses)
    if total == 0:
        return {"win_rate": 0.50, "avg_win_payout": 0.95, "avg_loss": 1.0, "avg_units": 1.0}

    win_rate = len(wins) / total

    avg_win_payout = 0.95
    if wins:
        payouts = [profit_for_result(w.get("odds", 0), 1.0, "WIN") for w in wins]
        avg_win_payout = sum(payouts) / len(payouts) if payouts else 0.95

    units = []
    for b in graded_bets:
        try:
            units.append(abs(float(b.get("units", 1.0))))
        except (TypeError, ValueError):
            units.append(1.0)
    avg_units = sum(units) / len(units) if units else 1.0

    return {
        "win_rate": win_rate,
        "avg_win_payout": max(avg_win_payout, 0.01),
        "avg_loss": 1.0,
        "avg_units": max(avg_units, 0.25),
    }


def _recent_graded_bets(graded_bets: List[dict]) -> List[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=MC_LOOKBACK_DAYS)
    recent = []
    for bet in graded_bets:
        raw_date = str(bet.get("graded_at") or bet.get("date") or "").strip()
        if not raw_date:
            continue
        parsed = _parse_datetime_utc(raw_date)
        if parsed is None:
            continue
        if parsed >= cutoff:
            recent.append(bet)
    return recent or graded_bets


def _run_single_sim(
    profile: Dict[str, float],
    bankroll: float,
    horizon: int,
    ruin_threshold: float,
) -> Dict[str, float]:
    balance = bankroll
    peak = bankroll
    max_drawdown = 0.0

    for _ in range(horizon):
        stake = profile["avg_units"]
        if random.random() < profile["win_rate"]:
            balance += stake * profile["avg_win_payout"]
        else:
            balance -= stake * profile["avg_loss"]

        peak = max(peak, balance)
        drawdown = (peak - balance) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)

        if balance <= ruin_threshold:
            return {"final": balance, "max_drawdown": max_drawdown, "ruined": 1.0}

    return {"final": balance, "max_drawdown": max_drawdown, "ruined": 0.0}


def run_monte_carlo() -> dict:
    graded = get_all_graded_bets()
    recent_graded = _recent_graded_bets(graded)
    profile = _estimate_bet_profile(recent_graded)

    if not graded:
        report = (
            "**MONTE CARLO RISK REPORT**\n"
            f"No graded bets found yet, so the simulation cannot run.\n"
            f"Lookback: {MC_LOOKBACK_DAYS} days | Required minimum: {MC_MIN_GRADED_BETS} graded bets"
        )
        if DISCORD_STATUS_WEBHOOK_URL:
            post_discord({"embeds": [{"description": report, "color": 16753920}]}, webhook_url=DISCORD_STATUS_WEBHOOK_URL)
        return {
            "detail": "no graded bets for simulation",
            "count": 0,
            "label": "updates",
        }

    if len(recent_graded) < MC_MIN_GRADED_BETS:
        report = (
            "**MONTE CARLO RISK REPORT**\n"
            f"Not enough recent graded bets to simulate reliably.\n"
            f"Recent graded bets: {len(recent_graded)} | Required minimum: {MC_MIN_GRADED_BETS}\n"
            f"Lookback: {MC_LOOKBACK_DAYS} days"
        )
        if DISCORD_STATUS_WEBHOOK_URL:
            post_discord({"embeds": [{"description": report, "color": 16753920}]}, webhook_url=DISCORD_STATUS_WEBHOOK_URL)
        return {
            "detail": f"monte carlo skipped | recent_graded={len(recent_graded)}",
            "count": len(recent_graded),
            "label": "updates",
            "meta": {"lookback_days": str(MC_LOOKBACK_DAYS)},
        }

    finals = []
    drawdowns = []
    ruins = 0

    for _ in range(MC_SIMULATIONS):
        result = _run_single_sim(profile, MC_BANKROLL_UNITS, MC_HORIZON_BETS, MC_RUIN_THRESHOLD)
        finals.append(result["final"])
        drawdowns.append(result["max_drawdown"])
        ruins += int(result["ruined"])

    finals.sort()
    p5 = finals[int(MC_SIMULATIONS * 0.05)]
    p25 = finals[int(MC_SIMULATIONS * 0.25)]
    median = finals[int(MC_SIMULATIONS * 0.50)]
    p75 = finals[int(MC_SIMULATIONS * 0.75)]
    p95 = finals[int(MC_SIMULATIONS * 0.95)]
    avg_drawdown = sum(drawdowns) / len(drawdowns)
    max_drawdown = max(drawdowns)
    ruin_pct = (ruins / MC_SIMULATIONS) * 100.0

    report = (
        f"**MONTE CARLO RISK REPORT**\n"
        f"Simulations: {MC_SIMULATIONS:,} | Horizon: {MC_HORIZON_BETS} bets\n"
        f"Starting Bankroll: {MC_BANKROLL_UNITS:.0f}u\n"
        f"Historical Win Rate: {profile['win_rate'] * 100:.1f}%\n"
        f"Avg Win Payout: {profile['avg_win_payout']:.2f}x | Avg Stake: {profile['avg_units']:.2f}u\n\n"
        f"**Projected Bankroll (percentiles)**\n"
        f"```\n"
        f" 5th: {p5:>8.1f}u\n"
        f"25th: {p25:>8.1f}u\n"
        f" 50th: {median:>8.1f}u\n"
        f"75th: {p75:>8.1f}u\n"
        f"95th: {p95:>8.1f}u\n"
        f"```\n"
        f"**Risk Metrics**\n"
        f"Risk of Ruin: **{ruin_pct:.2f}%**\n"
        f"Avg Max Drawdown: {avg_drawdown * 100:.1f}%\n"
        f"Worst Max Drawdown: {max_drawdown * 100:.1f}%"
    )

    if DISCORD_STATUS_WEBHOOK_URL:
        post_discord(
            {"embeds": [{"description": report, "color": 10181046}]},
            webhook_url=DISCORD_STATUS_WEBHOOK_URL,
        )

    return {
        "detail": f"monte carlo complete | ruin={ruin_pct:.2f}% | median={median:.1f}u | lookback={MC_LOOKBACK_DAYS}d",
        "count": MC_SIMULATIONS,
        "label": "updates",
        "meta": {"lookback_days": str(MC_LOOKBACK_DAYS), "recent_graded": str(len(recent_graded))},
    }


if __name__ == "__main__":
    print(run_monte_carlo())
