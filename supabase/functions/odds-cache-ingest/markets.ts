const BASEBALL_ONLY_MAIN_MARKETS = new Set([
  "h2h_1st_5_innings",
  "spreads_1st_5_innings",
  "totals_1st_5_innings",
  "runs_1st_inning",
]);

export function mainMarketsFor(sportKey: string, rawMarkets: string): string {
  const markets = rawMarkets
    .split(",")
    .map((market) => market.trim())
    .filter(Boolean);

  if (sportKey === "baseball_mlb") {
    // MLB main pulls can keep first-five moneyline/spread/total markets, but
    // the 1st-inning NRFI market belongs to the separate MLB NRFI model path.
    return markets.filter((market) => market !== "runs_1st_inning").join(",");
  }

  // Every non-MLB sport should drop baseball-only inning markets so a shared
  // default like `h2h,spreads,totals,h2h_1st_5_innings` cannot break WNBA or
  // other basketball pulls with a 422.
  return markets.filter((market) => !BASEBALL_ONLY_MAIN_MARKETS.has(market)).join(",");
}
