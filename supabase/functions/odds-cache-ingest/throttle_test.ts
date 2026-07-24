import { assertEquals } from "https://deno.land/std@0.224.0/assert/mod.ts";
import {
  pollIntervalMinutesFor,
  type ProximityConfig,
  sportDueThisSlot,
} from "./throttle.ts";

const CFG: ProximityConfig = {
  farHours: 24,
  midHours: 12,
  nearHours: 2,
  pollFarMinutes: 240,
  pollMidMinutes: 60,
  pollCloseMinutes: 15,
  pollUnknownMinutes: 60,
  cycleMinutes: 10,
};

Deno.test("poll interval maps proximity to the correct tier", () => {
  assertEquals(pollIntervalMinutesFor(48, CFG), 240); // >24h -> sparse
  assertEquals(pollIntervalMinutesFor(24, CFG), 60); // boundary -> mid
  assertEquals(pollIntervalMinutesFor(18, CFG), 60); // 12-24h -> hourly
  assertEquals(pollIntervalMinutesFor(6, CFG), 15); // 2-12h -> 15 min
  assertEquals(pollIntervalMinutesFor(1, CFG), 10); // <2h -> every tick
  assertEquals(pollIntervalMinutesFor(-0.5, CFG), 10); // live game -> every tick
  assertEquals(pollIntervalMinutesFor(null, CFG), 60); // unknown -> discovery
});

Deno.test("imminent games are due on every slot", () => {
  for (let slot = 0; slot < 10; slot++) {
    assertEquals(sportDueThisSlot(1, slot, 0, CFG), true);
  }
});

Deno.test("far games are due only every 24 slots (4h at 10-min cycle)", () => {
  const due: number[] = [];
  for (let slot = 0; slot < 48; slot++) {
    if (sportDueThisSlot(48, slot, 0, CFG)) due.push(slot);
  }
  assertEquals(due, [0, 24]);
});

Deno.test("mid-range games are due hourly (every 6 slots)", () => {
  const due: number[] = [];
  for (let slot = 0; slot < 18; slot++) {
    if (sportDueThisSlot(18, slot, 0, CFG)) due.push(slot);
  }
  assertEquals(due, [0, 6, 12]);
});

Deno.test("offset staggers sports so they don't all fire on the same far slot", () => {
  // Two far-out sports (24-slot cadence) with offsets 0 and 1 fire on
  // different slots.
  assertEquals(sportDueThisSlot(48, 0, 0, CFG), true);
  assertEquals(sportDueThisSlot(48, 0, 1, CFG), false);
  assertEquals(sportDueThisSlot(48, 1, 1, CFG), true);
});

Deno.test("negative offset arithmetic stays non-negative", () => {
  // slot < offset must not throw or produce a negative modulo.
  assertEquals(sportDueThisSlot(48, 0, 5, CFG), false);
  assertEquals(sportDueThisSlot(48, 5, 5, CFG), true);
});
