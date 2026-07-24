import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  hasGameWithinHorizon,
  isTennisToken,
  resolveSportsFromActive,
} from "./sports.ts";

const HOUR = 3_600_000;
const NOW = Date.parse("2026-07-24T00:00:00Z");
const iso = (hoursFromNow: number) =>
  new Date(NOW + hoursFromNow * HOUR).toISOString();

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

Deno.test("in-season when a game is within the horizon", () => {
  // MLB game tonight -> in season.
  assertEquals(hasGameWithinHorizon([{ commence_time: iso(3) }], NOW, 192), true);
  // NFL preseason game ~6 days out -> in season within the 8-day horizon.
  assertEquals(hasGameWithinHorizon([{ commence_time: iso(144) }], NOW, 192), true);
});

Deno.test("out of season when no game (futures-only) within horizon", () => {
  // Empty events list (only outrights exist -> not returned by /events).
  assertEquals(hasGameWithinHorizon([], NOW, 192), false);
  // Lone game far beyond the horizon (e.g. a schedule stub) -> not in season.
  assertEquals(
    hasGameWithinHorizon([{ commence_time: iso(24 * 30) }], NOW, 192),
    false,
  );
});

Deno.test("counts a live/just-started game via the grace window", () => {
  assertEquals(hasGameWithinHorizon([{ commence_time: iso(-1) }], NOW, 192), true);
  // Long-finished game outside the grace window does not count.
  assertEquals(
    hasGameWithinHorizon([{ commence_time: iso(-48) }], NOW, 192),
    false,
  );
});

Deno.test("ignores unparseable commence_time values", () => {
  assertEquals(
    hasGameWithinHorizon(
      [{ commence_time: "not-a-date" }, {}, { commence_time: iso(2) }],
      NOW,
      192,
    ),
    true,
  );
  assertEquals(
    hasGameWithinHorizon([{ commence_time: "not-a-date" }, {}], NOW, 192),
    false,
  );
});
