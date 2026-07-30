export function mainMarketsFor(sportKey: string, rawMarkets: string): string {
  if (sportKey !== "baseball_mlb") {
    return rawMarkets;
  }
  // MLB main pulls do not support runs_1st_inning on The Odds API. Keep the
  // market in the separate NRFI model path, but strip it from the ingest pull
  // so the main cache fetch cannot fail with HTTP 422.
  return rawMarkets
    .split(",")
    .map((market) => market.trim())
    .filter((market) => market && market !== "runs_1st_inning")
    .join(",");
}
