// Pure sport-resolution logic (no I/O) so it can be unit-tested directly.
//
// The syndicate cares about a fixed UNIVERSE of leagues (MLB, NBA, WNBA, NHL,
// NFL, tennis). Which of those actually have games changes throughout the year,
// so instead of hand-maintaining an "active sports" secret we auto-detect it.
//
// IMPORTANT: we detect a league as in-season by whether it has real, dated
// GAMES on the free /v4/sports/{key}/events endpoint — NOT by the /v4/sports
// "active" flag. That flag stays true year-round for leagues that post futures/
// outrights (e.g. NFL Super Bowl winner, NHL Stanley Cup) even in the dead of
// their off-season, so gating on it would burn main-pull credits on leagues
// with zero games. The /events endpoint lists only actual game events (never
// outrights) and costs 0 credits, so it is the correct in-season signal.
//
// Tennis is different: keys are per-tournament and discovered from the free
// /v4/sports listing (resolveSportsFromActive), then expanded from the tennis
// umbrella tokens.

export function isTennisToken(token: string): boolean {
  return token === "tennis" || token === "tennis_atp" || token === "tennis_wta";
}

/**
 * Resolve the universe of desired leagues against the set of currently-active
 * sport keys (as returned by The Odds API /v4/sports).
 *
 * - Concrete keys (baseball_mlb, basketball_nba, ...) are kept only if they are
 *   currently active (in season).
 * - Tennis umbrella tokens (tennis / tennis_atp / tennis_wta) expand to every
 *   active per-tournament tennis key (tennis_atp_*, tennis_wta_*), since The
 *   Odds API keys tennis per-tournament and those rotate week to week.
 *
 * Universe order is preserved (it doubles as ROI priority for the run's job
 * ordering), and duplicates are removed.
 */
export function resolveSportsFromActive(
  universe: string[],
  activeKeys: string[],
): string[] {
  const activeSet = new Set(activeKeys);
  const resolved: string[] = [];
  for (const token of universe) {
    if (isTennisToken(token)) {
      const tourFilter = token === "tennis_atp"
        ? (key: string) => key.startsWith("tennis_atp")
        : token === "tennis_wta"
        ? (key: string) => key.startsWith("tennis_wta")
        : (_key: string) => true;
      for (const key of activeKeys) {
        if (
          key.startsWith("tennis") && tourFilter(key) &&
          !resolved.includes(key)
        ) {
          resolved.push(key);
        }
      }
    } else if (activeSet.has(token) && !resolved.includes(token)) {
      resolved.push(token);
    }
  }
  return resolved;
}

/**
 * True if the given events list contains at least one real game commencing
 * within [now - liveGraceHours, now + horizonHours]. Used to decide whether a
 * league actually has games right now (in season) vs. only futures/outrights.
 * `commence_time` values that don't parse are ignored.
 */
export function hasGameWithinHorizon(
  events: Array<{ commence_time?: string }>,
  nowMs: number,
  horizonHours: number,
  liveGraceHours = 6,
): boolean {
  const lower = nowMs - liveGraceHours * 3_600_000;
  const upper = nowMs + horizonHours * 3_600_000;
  for (const event of events) {
    const ts = Date.parse(String(event.commence_time ?? ""));
    if (Number.isFinite(ts) && ts >= lower && ts <= upper) return true;
  }
  return false;
}
