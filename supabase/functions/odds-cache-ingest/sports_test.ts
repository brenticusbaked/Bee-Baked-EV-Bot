import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { isTennisToken, resolveSportsFromActive } from "./sports.ts";

const UNIVERSE = [
  "baseball_mlb",
  "basketball_wnba",
  "basketball_nba",
  "icehockey_nhl",
  "americanfootball_nfl",
  "tennis",
];

Deno.test("tennis token detection", () => {
  assertEquals(isTennisToken("tennis"), true);
  assertEquals(isTennisToken("tennis_atp"), true);
  assertEquals(isTennisToken("tennis_wta"), true);
  assertEquals(isTennisToken("tennis_atp_wimbledon"), false);
  assertEquals(isTennisToken("baseball_mlb"), false);
});

Deno.test("keeps only in-season concrete leagues, preserving priority order", () => {
  // Summer: MLB + WNBA in season, NBA/NHL/NFL out, some tennis events live.
  const active = [
    "baseball_mlb",
    "basketball_wnba",
    "tennis_atp_citi_open",
    "tennis_wta_prague",
  ];
  assertEquals(resolveSportsFromActive(UNIVERSE, active), [
    "baseball_mlb",
    "basketball_wnba",
    "tennis_atp_citi_open",
    "tennis_wta_prague",
  ]);
});

Deno.test("NBA/NHL auto-appear when their seasons open, no config change", () => {
  // Winter: MLB out, NBA/NHL in.
  const active = ["basketball_nba", "icehockey_nhl", "basketball_wnba"];
  assertEquals(resolveSportsFromActive(UNIVERSE, active), [
    "basketball_wnba",
    "basketball_nba",
    "icehockey_nhl",
  ]);
});

Deno.test("tennis umbrella expands to every active tennis tournament key", () => {
  const active = [
    "baseball_mlb",
    "tennis_atp_us_open",
    "tennis_wta_us_open",
    "tennis_atp_winston_salem",
  ];
  assertEquals(resolveSportsFromActive(["tennis"], active), [
    "tennis_atp_us_open",
    "tennis_wta_us_open",
    "tennis_atp_winston_salem",
  ]);
});

Deno.test("tennis_atp / tennis_wta umbrellas filter by tour", () => {
  const active = ["tennis_atp_us_open", "tennis_wta_us_open"];
  assertEquals(resolveSportsFromActive(["tennis_atp"], active), [
    "tennis_atp_us_open",
  ]);
  assertEquals(resolveSportsFromActive(["tennis_wta"], active), [
    "tennis_wta_us_open",
  ]);
});

Deno.test("empty active list yields nothing (all out of season)", () => {
  assertEquals(resolveSportsFromActive(UNIVERSE, []), []);
});

Deno.test("ignores active sports outside the universe", () => {
  const active = ["baseball_mlb", "soccer_epl", "mma_mixed_martial_arts"];
  assertEquals(resolveSportsFromActive(UNIVERSE, active), ["baseball_mlb"]);
});

Deno.test("de-duplicates without losing order", () => {
  const active = ["baseball_mlb", "baseball_mlb", "tennis_atp_x", "tennis_atp_x"];
  assertEquals(resolveSportsFromActive(["baseball_mlb", "tennis"], active), [
    "baseball_mlb",
    "tennis_atp_x",
  ]);
});
