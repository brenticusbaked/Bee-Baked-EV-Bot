import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { mainMarketsFor } from "./markets.ts";

Deno.test("mlb main markets strip runs_1st_inning", () => {
  const raw = "h2h,spreads,totals,h2h_1st_5_innings,runs_1st_inning";
  assertEquals(
    mainMarketsFor("baseball_mlb", raw),
    "h2h,spreads,totals,h2h_1st_5_innings",
  );
});

Deno.test("wnba main markets strip baseball-only inning markets", () => {
  const raw = "h2h,spreads,totals,h2h_1st_5_innings,runs_1st_inning";
  assertEquals(mainMarketsFor("basketball_wnba", raw), "h2h,spreads,totals");
});

Deno.test("non-mlb main markets stay unchanged", () => {
  const raw = "h2h,spreads,totals";
  assertEquals(mainMarketsFor("basketball_nba", raw), raw);
});
