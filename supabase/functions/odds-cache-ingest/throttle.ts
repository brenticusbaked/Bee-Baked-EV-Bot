// Game-proximity throttle logic for the odds-cache-ingest Edge Function.
//
// Pure, side-effect-free helpers so the credit-scheduling behaviour can be unit
// tested without importing the full function (which starts an HTTP server on
// import). See index.ts for how these are wired to env config and pg_cron.

export type ProximityConfig = {
  // Hour cutoffs (hours until nearest game) that define the tiers.
  farHours: number;
  midHours: number;
  nearHours: number;
  // Target minutes between pulls for each tier.
  pollFarMinutes: number;
  pollMidMinutes: number;
  pollCloseMinutes: number;
  // Cadence used when no fixture is cached yet (discovery).
  pollUnknownMinutes: number;
  // Length of one rotation slot in minutes.
  cycleMinutes: number;
};

// Map hours-until-nearest-game to a target poll interval in minutes. A larger
// interval means the sport is pulled less often (spends fewer credits).
export function pollIntervalMinutesFor(
  hoursToGame: number | null,
  cfg: ProximityConfig,
): number {
  if (hoursToGame === null) return cfg.pollUnknownMinutes;
  if (hoursToGame > cfg.farHours) return cfg.pollFarMinutes;
  if (hoursToGame > cfg.midHours) return cfg.pollMidMinutes;
  if (hoursToGame > cfg.nearHours) return cfg.pollCloseMinutes;
  // <= nearHours, including in-play games with a slightly-past commence time:
  // pull on every tick.
  return cfg.cycleMinutes;
}

// Whether a sport is due for a pull on this slot given its game proximity. The
// per-sport offset staggers sports so they don't all fire on the same tick.
export function sportDueThisSlot(
  hoursToGame: number | null,
  slot: number,
  offset: number,
  cfg: ProximityConfig,
): boolean {
  const intervalMinutes = pollIntervalMinutesFor(hoursToGame, cfg);
  const slotsPerPoll = Math.max(1, Math.round(intervalMinutes / cfg.cycleMinutes));
  return (((slot - offset) % slotsPerPoll) + slotsPerPoll) % slotsPerPoll === 0;
}
