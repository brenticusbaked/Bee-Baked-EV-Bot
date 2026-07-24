// Pure sport-resolution logic (no I/O) so it can be unit-tested directly.
//
// The syndicate cares about a fixed UNIVERSE of leagues (MLB, NBA, WNBA, NHL,
// NFL, tennis). Which of those are actually in season changes throughout the
// year, so instead of hand-maintaining an "active sports" secret we intersect
// the universe with the leagues The Odds API currently lists as active (its
// free /v4/sports endpoint returns only in-season sports). Off-season leagues
// simply drop out and cost zero credits; a league auto-switches back on the
// moment its season opens — no config edits ever.

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
