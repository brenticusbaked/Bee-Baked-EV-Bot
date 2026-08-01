import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "npm:@supabase/supabase-js@2.45.4";
import { type ProximityConfig, sportDueThisSlot } from "./throttle.ts";
import {
  hasGameWithinHorizon,
  isTennisToken,
  resolveSportsFromActive,
} from "./sports.ts";
import { mainMarketsFor as filterMainMarkets } from "./markets.ts";

type OddsOutcome = {
  name: string;
  price: number;
  point?: number;
  description?: string;
};

type OddsMarket = {
  key: string;
  last_update?: string;
  outcomes?: OddsOutcome[];
};

type Bookmaker = {
  key: string;
  title?: string;
  last_update?: string;
  markets?: OddsMarket[];
};

type OddsEvent = {
  id: string;
  sport_key?: string;
  commence_time?: string;
  home_team?: string;
  away_team?: string;
  bookmakers?: Bookmaker[];
};

type IngestJob = {
  kind: "main" | "enrich";
  sportKey: string;
  markets: string;
  regions: string;
  bookmakers?: string;
  estimatedCredits: number;
  sharpRegions?: string;
  sharpBookmakers?: string;
  softRegions?: string;
  softBookmakers?: string;
  perEventCredits?: number;
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

// Expanded Tiered Key Pool (added ODDS_API_KEY_5)
const ODDS_API_KEYS = [
  Deno.env.get("ODDS_API_KEY") ?? "",
  Deno.env.get("ODDS_API_KEY_2") ?? "",
  Deno.env.get("ODDS_API_KEY_3") ?? "",
  Deno.env.get("ODDS_API_KEY_4") ?? "",
  Deno.env.get("ODDS_API_KEY_5") ?? "",
].map((key) => key.trim()).filter(Boolean);

const INGEST_SECRET = (Deno.env.get("ODDS_INGEST_FUNCTION_SECRET") ?? "").trim();
const REGIONS = Deno.env.get("ODDS_API_REGIONS") ?? "us,eu";
const BOOKMAKERS = Deno.env.get("ODDS_API_TARGET_BOOKS") ??
  "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,bovada,espnbet,fanatics,betrivers";
const MAIN_MARKETS = Deno.env.get("ODDS_API_MARKETS") ?? "h2h,spreads,totals";
const WNBA_PROP_MARKETS = Deno.env.get("ODDS_API_WNBA_PROP_MARKETS") ??
  "player_points,player_rebounds,player_assists,player_threes,player_blocks,player_steals,player_turnovers,player_points_rebounds_assists,player_points_rebounds,player_points_assists,player_rebounds_assists,player_field_goals,player_frees_made,player_frees_attempts";
const NFL_PROP_MARKETS = Deno.env.get("ODDS_API_NFL_PROP_MARKETS") ??
  "player_pass_attempts,player_pass_completions,player_pass_interceptions,player_pass_longest_completion,player_pass_rush_yds,player_pass_rush_reception_tds,player_pass_rush_reception_yds,player_pass_tds,player_pass_yds,player_pass_yds_q1,player_pats,player_receptions,player_reception_longest,player_reception_tds,player_reception_yds,player_rush_attempts,player_rush_longest,player_rush_reception_tds,player_rush_reception_yds,player_rush_tds,player_rush_yds,player_sacks,player_solo_tackles,player_tackles_assists,player_kicking_points,player_field_goals,player_defensive_interceptions";

const TENNIS_MAIN_MARKETS = Deno.env.get("ODDS_API_TENNIS_MARKETS") ?? "h2h";
function mainMarketsFor(sportKey: string): string {
  const rawMarkets = sportKey.startsWith("tennis") ? TENNIS_MAIN_MARKETS : MAIN_MARKETS;
  return filterMainMarkets(sportKey, rawMarkets);
}

const ENRICH_REGIONS = Deno.env.get("ODDS_API_ENRICH_REGIONS") ?? "us";
const SHARP_BOOK = Deno.env.get("ODDS_API_SHARP_BOOK") ?? "pinnacle";
const ENRICH_SHARP_REGIONS = Deno.env.get("ODDS_API_ENRICH_SHARP_REGIONS") ?? "eu";
const SOFT_BOOKMAKERS = (Deno.env.get("ODDS_API_TARGET_BOOKS") ??
  "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,bovada,espnbet,fanatics,betrivers")
  .split(",")
  .map((book) => book.trim())
  .filter((book) => book && book !== SHARP_BOOK)
  .join(",");

const EXTENDED_REGIONS = Deno.env.get("ODDS_API_EXTENDED_REGIONS") ?? "us_ex";
const EXTENDED_BOOKS = Deno.env.get("ODDS_API_EXTENDED_BOOKS") ??
  "novig,kalshi,polymarket,prophetx";
const EXTENDED_EVERY_N_SLOTS = Number(Deno.env.get("ODDS_EXTENDED_EVERY_N_SLOTS") ?? "0");
const ENABLE_MARKET_ENRICHMENT =
  (Deno.env.get("ENABLE_MARKET_ENRICHMENT") ?? "true").toLowerCase() !== "false";

const BURN_UNTIL = (Deno.env.get("ODDS_BURN_UNTIL") ?? "").trim();
const BURN_ACTIVE = BURN_UNTIL !== "" && new Date().toISOString().slice(0, 10) <= BURN_UNTIL;
const BURN_MAX_CREDITS_PER_RUN = Number(Deno.env.get("ODDS_BURN_MAX_CREDITS_PER_RUN") ?? "150");
const BURN_MAX_EVENTS_PER_ENRICH = Number(Deno.env.get("ODDS_BURN_MAX_EVENTS_PER_ENRICH") ?? "6");

const MAX_CREDITS_PER_RUN = BURN_ACTIVE
  ? BURN_MAX_CREDITS_PER_RUN
  : Number(Deno.env.get("ODDS_MAX_CREDITS_PER_RUN") ?? "48");
const MAX_EVENTS_PER_ENRICH = BURN_ACTIVE
  ? BURN_MAX_EVENTS_PER_ENRICH
  : Number(Deno.env.get("ODDS_MAX_EVENTS_PER_ENRICH") ?? "4");
const CYCLE_MINUTES = Number(Deno.env.get("ODDS_CYCLE_MINUTES") ?? "10");

const PROXIMITY_THROTTLE = !BURN_ACTIVE &&
  (Deno.env.get("ODDS_PROXIMITY_THROTTLE") ?? "true").toLowerCase() !== "false";
const PROXIMITY_FAR_HOURS = Number(Deno.env.get("ODDS_PROXIMITY_FAR_HOURS") ?? "24");
const PROXIMITY_MID_HOURS = Number(Deno.env.get("ODDS_PROXIMITY_MID_HOURS") ?? "12");
const PROXIMITY_NEAR_HOURS = Number(Deno.env.get("ODDS_PROXIMITY_NEAR_HOURS") ?? "2");
const POLL_FAR_MINUTES = Number(Deno.env.get("ODDS_POLL_FAR_MINUTES") ?? "240");
const POLL_MID_MINUTES = Number(Deno.env.get("ODDS_POLL_MID_MINUTES") ?? "60");
const POLL_CLOSE_MINUTES = Number(Deno.env.get("ODDS_POLL_CLOSE_MINUTES") ?? "15");
const POLL_UNKNOWN_MINUTES = Number(Deno.env.get("ODDS_POLL_UNKNOWN_MINUTES") ?? "60");

const PROXIMITY_CONFIG: ProximityConfig = {
  farHours: PROXIMITY_FAR_HOURS,
  midHours: PROXIMITY_MID_HOURS,
  nearHours: PROXIMITY_NEAR_HOURS,
  pollFarMinutes: POLL_FAR_MINUTES,
  pollMidMinutes: POLL_MID_MINUTES,
  pollCloseMinutes: POLL_CLOSE_MINUTES,
  pollUnknownMinutes: POLL_UNKNOWN_MINUTES,
  cycleMinutes: CYCLE_MINUTES,
};

const SPORT_UNIVERSE_BASE = (Deno.env.get("ODDS_API_SPORT_UNIVERSE") ??
  "baseball_mlb,basketball_wnba,basketball_nba,icehockey_nhl,americanfootball_nfl,tennis")
  .split(",")
  .map((sport) => sport.trim())
  .filter(Boolean);

const ENABLE_TENNIS_SCAN =
  (Deno.env.get("ENABLE_TENNIS_SCAN") ?? "true").toLowerCase() !== "false";
const ENABLE_SOCCER_SCAN =
  (Deno.env.get("ENABLE_SOCCER_SCAN") ?? "false").toLowerCase() === "true";
const SOCCER_LEAGUES_FILTER = (Deno.env.get("SOCCER_LEAGUES_FILTER") ??
  "soccer_epl,soccer_usa_mls,soccer_uefa_champs_league,soccer_spain_la_liga")
  .split(",")
  .map((league) => league.trim())
  .filter(Boolean);

const SPORT_UNIVERSE = (() => {
  let universe = [...SPORT_UNIVERSE_BASE];
  if (!ENABLE_TENNIS_SCAN) {
    universe = universe.filter((sport) => !sport.startsWith("tennis"));
  }
  if (ENABLE_SOCCER_SCAN) {
    for (const league of SOCCER_LEAGUES_FILTER) {
      if (!universe.includes(league)) universe.push(league);
    }
  }
  return universe;
})();

const AUTODETECT_SPORTS =
  (Deno.env.get("ODDS_API_AUTODETECT_SPORTS") ?? "true").toLowerCase() !==
    "false";
const MANUAL_SPORTS = (Deno.env.get("ODDS_API_ACTIVE_SPORTS") ?? "")
  .split(",")
  .map((sport) => sport.trim())
  .filter(Boolean);

const SEASON_HORIZON_HOURS = Number(
  Deno.env.get("ODDS_SEASON_HORIZON_HOURS") ?? "192",
);

async function leagueHasGames(sportKey: string): Promise<boolean> {
  try {
    const url = new URL(
      `https://api.the-odds-api.com/v4/sports/${sportKey}/events`,
    );
    url.searchParams.set("dateFormat", "iso");
    const response = await oddsFetch(url);
    if (!response.ok) return false;
    const events = (await response.json()) as Array<{ commence_time?: string }>;
    return hasGameWithinHorizon(events, Date.now(), SEASON_HORIZON_HOURS);
  } catch (_error) {
    return false;
  }
}

async function fetchSportKeys(all: boolean): Promise<string[] | null> {
  try {
    const url = new URL("https://api.the-odds-api.com/v4/sports/");
    if (all) url.searchParams.set("all", "true");
    const response = await oddsFetch(url);
    if (!response.ok) return null;
    const listed = (await response.json()) as Array<
      { key: string; group?: string; active?: boolean }
    >;
    return listed
      .filter((sport) => sport.active !== false)
      .map((sport) => sport.key);
  } catch (_error) {
    return null;
  }
}

async function resolveActiveSports(universe: string[]): Promise<string[]> {
  const wantsTennis = universe.some(isTennisToken);
  let tennisCandidates: string[] = [];
  if (wantsTennis) {
    const allKeys = (await fetchSportKeys(true)) ?? [];
    const tennisKeys = allKeys.filter((key) => key.startsWith("tennis"));
    tennisCandidates = resolveSportsFromActive(
      universe.filter(isTennisToken),
      tennisKeys,
    );
  }

  const concrete = universe.filter((token) => !isTennisToken(token));
  const gateTargets = [...concrete, ...tennisCandidates];
  const gates = await Promise.all(
    gateTargets.map(async (key) => [key, await leagueHasGames(key)] as const),
  );
  const gamesByKey = new Map(gates);

  const resolved: string[] = [];
  for (const token of universe) {
    if (isTennisToken(token)) {
      for (const key of tennisCandidates) {
        if (gamesByKey.get(key) && !resolved.includes(key)) resolved.push(key);
      }
    } else if (gamesByKey.get(token) && !resolved.includes(token)) {
      resolved.push(token);
    }
  }
  return resolved;
}

type SportExtras = { groups: string[] };
const SPORT_EXTRAS: Record<string, SportExtras> = {
  baseball_mlb: {
    groups: [
      "pitcher_strikeouts,pitcher_outs,pitcher_hits_allowed,pitcher_earned_runs",
      "batter_hits,batter_total_bases,batter_home_runs,batter_rbis,batter_runs_scored",
      "runs_1st_inning",
      "batter_strikeouts",
    ],
  },
  basketball_wnba: {
    groups: [
      WNBA_PROP_MARKETS,
      "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    ],
  },
  basketball_nba: {
    groups: [
      "player_points,player_rebounds,player_assists,player_threes,player_points_rebounds_assists",
      "player_points_rebounds,player_points_assists,player_rebounds_assists,player_blocks,player_steals",
      "player_turnovers",
      "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    ],
  },
  americanfootball_nfl: {
    groups: [
      NFL_PROP_MARKETS,
      "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    ],
  },
  icehockey_nhl: {
    groups: [
      "player_points,player_goals,player_assists,player_shots_on_goal,player_total_saves",
      "player_blocked_shots,player_power_play_points",
    ],
  },
};

const SOCCER_PROP_MARKETS = Deno.env.get("ODDS_API_SOCCER_PROP_MARKETS") ??
  "player_shots_on_target,player_shots";
const SOCCER_EXTRAS: SportExtras = {
  groups: [SOCCER_PROP_MARKETS],
};

function extrasFor(sportKey: string): SportExtras | undefined {
  if (SPORT_EXTRAS[sportKey]) return SPORT_EXTRAS[sportKey];
  if (sportKey.startsWith("soccer_")) return SOCCER_EXTRAS;
  return undefined;
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false },
});

function creditsFor(markets: string, regions: string): number {
  const marketCount = markets.split(",").filter(Boolean).length;
  const regionCount = regions.split(",").filter(Boolean).length;
  return Math.max(1, marketCount * regionCount);
}

function rotate<T>(items: T[], offset: number): T[] {
  if (items.length === 0) return items;
  const shift = ((offset % items.length) + items.length) % items.length;
  return [...items.slice(shift), ...items.slice(0, shift)];
}

function currentSlot(): number {
  return Math.floor(Date.now() / (CYCLE_MINUTES * 60 * 1000));
}

const UPCOMING_HORIZON_HOURS = Number(
  Deno.env.get("ODDS_UPCOMING_HORIZON_HOURS") ?? "72",
);

async function getUpcomingFixtureIds(sportKey: string): Promise<string[]> {
  const lookbackHours = 6;
  const horizonHours = UPCOMING_HORIZON_HOURS;
  const since = new Date(Date.now() - lookbackHours * 3600 * 1000).toISOString();
  const until = new Date(Date.now() + horizonHours * 3600 * 1000).toISOString();
  const { data, error } = await supabase
    .from("fixtures")
    .select("id, commence_time")
    .eq("sport_key", sportKey)
    .gte("commence_time", since)
    .lte("commence_time", until)
    .order("commence_time", { ascending: true });
  if (error || !data) return [];
  return data.map((row) => String(row.id));
}

async function hoursUntilNearestGame(sportKey: string): Promise<number | null> {
  const now = Date.now();
  const lookbackHours = 3;
  const horizonHours = 72;
  const since = new Date(now - lookbackHours * 3600 * 1000).toISOString();
  const until = new Date(now + horizonHours * 3600 * 1000).toISOString();
  const { data, error } = await supabase
    .from("fixtures")
    .select("commence_time")
    .eq("sport_key", sportKey)
    .gte("commence_time", since)
    .lte("commence_time", until)
    .order("commence_time", { ascending: true })
    .limit(1);
  if (error || !data || data.length === 0) return null;
  const ts = Date.parse(String(data[0].commence_time));
  if (!Number.isFinite(ts)) return null;
  return (ts - now) / 3_600_000;
}

function buildJobs(slot: number, sports: string[]): IngestJob[] {
  const mainJobs: IngestJob[] = sports.map((sportKey) => ({
    kind: "main",
    sportKey,
    markets: mainMarketsFor(sportKey),
    regions: REGIONS,
    estimatedCredits: creditsFor(mainMarketsFor(sportKey), REGIONS),
  }));

  const extendedJobs: IngestJob[] = [];
  if (
    EXTENDED_EVERY_N_SLOTS > 0 &&
    EXTENDED_REGIONS &&
    EXTENDED_BOOKS &&
    slot % EXTENDED_EVERY_N_SLOTS === 0
  ) {
    for (const sportKey of sports) {
      extendedJobs.push({
        kind: "main",
        sportKey,
        markets: mainMarketsFor(sportKey),
        regions: EXTENDED_REGIONS,
        bookmakers: EXTENDED_BOOKS,
        estimatedCredits: creditsFor(mainMarketsFor(sportKey), EXTENDED_REGIONS),
      });
    }
  }

  const enrichJobs: IngestJob[] = [];
  if (ENABLE_MARKET_ENRICHMENT) {
    for (const sportKey of sports) {
      const extras = extrasFor(sportKey);
      if (!extras) continue;
      for (const group of extras.groups) {
        if (!group) continue;
        const pairCredits =
          creditsFor(group, ENRICH_SHARP_REGIONS) + creditsFor(group, ENRICH_REGIONS);
        enrichJobs.push({
          kind: "enrich",
          sportKey,
          markets: group,
          regions: `${ENRICH_SHARP_REGIONS},${ENRICH_REGIONS}`,
          estimatedCredits: pairCredits,
          perEventCredits: pairCredits,
          sharpRegions: ENRICH_SHARP_REGIONS,
          sharpBookmakers: SHARP_BOOK,
          softRegions: ENRICH_REGIONS,
          softBookmakers: SOFT_BOOKMAKERS,
        });
      }
    }
  }

  return [
    ...rotate(mainJobs, slot),
    ...rotate(extendedJobs, slot),
    ...rotate(enrichJobs, slot),
  ];
}

// Improved oddsFetch: checks both status code and body text for quota expiration strings
async function oddsFetch(url: URL): Promise<Response> {
  let lastResponse: Response | null = null;
  for (const key of ODDS_API_KEYS) {
    url.searchParams.set("apiKey", key);
    const response = await fetch(url);
    
    // Clone response to inspect body without consuming it
    const clone = response.clone();
    let bodyText = "";
    try {
      bodyText = await clone.text();
    } catch (_e) {
      // ignore body read issues
    }

    const isQuotaError = 
      [401, 402, 429].includes(response.status) || 
      bodyText.includes("OUT_OF_USAGE_CREDITS");

    if (!isQuotaError) {
      return response;
    }
    
    console.warn(`API Key ending in ...${key.slice(-4)} exhausted or hit quota limit. Rotating...`);
    lastResponse = response;
  }
  if (lastResponse) return lastResponse;
  throw new Error("no Odds API keys configured");
}

async function fetchMain(job: IngestJob): Promise<{ events: OddsEvent[]; creditsUsed: number; remaining: number | null }> {
  const url = new URL(`https://api.the-odds-api.com/v4/sports/${job.sportKey}/odds`);
  url.searchParams.set("regions", job.regions);
  url.searchParams.set("markets", job.markets);
  url.searchParams.set("bookmakers", job.bookmakers ?? BOOKMAKERS);
  url.searchParams.set("oddsFormat", "decimal");

  const response = await oddsFetch(url);
  const remaining = numericHeader(response, "x-requests-remaining");
  const lastCost = numericHeader(response, "x-requests-last");
  if (!response.ok) {
    throw new Error(`${job.sportKey} main fetch failed: ${response.status} ${await response.text()}`);
  }
  const events = (await response.json()) as OddsEvent[];
  return { events, creditsUsed: lastCost ?? job.estimatedCredits, remaining };
}

async function fetchEventOdds(
  sportKey: string,
  eventId: string,
  markets: string,
  regions: string,
  bookmakers: string = BOOKMAKERS,
): Promise<{ event: OddsEvent | null; creditsUsed: number; remaining: number | null }> {
  const url = new URL(`https://api.the-odds-api.com/v4/sports/${sportKey}/events/${eventId}/odds`);
  url.searchParams.set("regions", regions);
  url.searchParams.set("markets", markets);
  url.searchParams.set("bookmakers", bookmakers);
  url.searchParams.set("oddsFormat", "decimal");

  const response = await oddsFetch(url);
  const remaining = numericHeader(response, "x-requests-remaining");
  const lastCost = numericHeader(response, "x-requests-last");
  if (response.status === 404 || response.status === 422) {
    return { event: null, creditsUsed: lastCost ?? creditsFor(markets, regions), remaining };
  }
  if (!response.ok) {
    throw new Error(`${sportKey}/${eventId} enrich fetch failed: ${response.status} ${await response.text()}`);
  }
  const event = (await response.json()) as OddsEvent;
  return { event, creditsUsed: lastCost ?? creditsFor(markets, regions), remaining };
}

function numericHeader(response: Response, name: string): number | null {
  const raw = response.headers.get(name);
  if (raw === null || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

async function upsertFixtures(sportKey: string, events: OddsEvent[]): Promise<number> {
  const fixtureRows = events.map((event) => ({
    id: event.id,
    sport_key: event.sport_key ?? sportKey,
    commence_time: event.commence_time ?? null,
    home_team: event.home_team ?? null,
    away_team: event.away_team ?? null,
    status: "scheduled",
    raw_event: event,
    updated_at: new Date().toISOString(),
  }));
  if (!fixtureRows.length) return 0;
  const { error } = await supabase.from("fixtures").upsert(fixtureRows, { onConflict: "id" });
  if (error) throw error;
  return fixtureRows.length;
}

async function latestUpdateMap(fixtureIds: string[]): Promise<Map<string, number>> {
  const map = new Map<string, number>();
  if (!fixtureIds.length) return map;
  const { data, error } = await supabase
    .from("historical_odds")
    .select("fixture_id, bookmaker_key, market_key, last_update")
    .in("fixture_id", fixtureIds)
    .not("last_update", "is", null)
    .order("last_update", { ascending: false })
    .limit(5000);
  if (error || !data) return map;
  for (const row of data) {
    const key = `${row.fixture_id}|${row.bookmaker_key}|${row.market_key}`;
    const ts = Date.parse(String(row.last_update));
    if (!Number.isFinite(ts)) continue;
    const existing = map.get(key);
    if (existing === undefined || ts > existing) map.set(key, ts);
  }
  return map;
}

function lineHash(
  eventId: string,
  bookmakerKey: string,
  marketKey: string,
  outcome: OddsOutcome,
  lastUpdate: string,
): string {
  return [
    eventId,
    bookmakerKey,
    marketKey,
    (outcome.description ?? "").toLowerCase().trim(),
    outcome.name.toLowerCase().trim(),
    String(outcome.point ?? ""),
    String(outcome.price),
    lastUpdate,
  ].join("|");
}

async function upsertOdds(sportKey: string, events: OddsEvent[]): Promise<number> {
  if (!events.length) return 0;
  const fixtureIds = events.map((event) => event.id);
  const seen = await latestUpdateMap(fixtureIds);
  const capturedAt = new Date().toISOString();
  const oddsRows: Record<string, unknown>[] = [];

  for (const event of events) {
    for (const bookmaker of event.bookmakers ?? []) {
      for (const market of bookmaker.markets ?? []) {
        const marketLastUpdate = market.last_update ?? bookmaker.last_update ?? capturedAt;
        const marketTs = Date.parse(marketLastUpdate);
        const key = `${event.id}|${bookmaker.key}|${market.key}`;
        const storedTs = seen.get(key);
        if (storedTs !== undefined && Number.isFinite(marketTs) && marketTs <= storedTs) {
          continue;
        }
        for (const outcome of market.outcomes ?? []) {
          if (typeof outcome.price !== "number") continue;
          oddsRows.push({
            fixture_id: event.id,
            sport_key: event.sport_key ?? sportKey,
            bookmaker_key: bookmaker.key,
            bookmaker_title: bookmaker.title ?? bookmaker.key,
            market_key: market.key,
            outcome_name: outcome.name,
            outcome_description: outcome.description ?? null,
            point: outcome.point ?? null,
            price_decimal: outcome.price,
            line_hash: lineHash(event.id, bookmaker.key, market.key, outcome, marketLastUpdate),
            last_update: marketLastUpdate,
            captured_at: capturedAt,
            raw_outcome: outcome,
          });
        }
      }
    }
  }

  if (!oddsRows.length) return 0;
  const { error } = await supabase
    .from("historical_odds")
    .upsert(oddsRows, { onConflict: "line_hash", ignoreDuplicates: true });
  if (error) throw error;
  return oddsRows.length;
}

serve(async (request) => {
  if (INGEST_SECRET && (request.headers.get("x-ingest-secret") ?? "").trim() !== INGEST_SECRET) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  }
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || ODDS_API_KEYS.length === 0) {
    return new Response(JSON.stringify({ error: "missing required environment variables" }), { status: 500 });
  }

  let forceRun = false;
  let debug = "";
  try {
    const body = await request.json();
    const trigger = String((body as { trigger?: unknown })?.trigger ?? "");
    forceRun = (body as { force?: unknown })?.force === true ||
      trigger.startsWith("manual");
    debug = String((body as { debug?: unknown })?.debug ?? "");
  } catch (_error) {
    // No/invalid JSON body
  }

  if (debug === "sports") {
    const inSeason = await fetchSportKeys(false);
    const allKeys = await fetchSportKeys(true);
    const universe = (!AUTODETECT_SPORTS && MANUAL_SPORTS.length > 0)
      ? MANUAL_SPORTS
      : SPORT_UNIVERSE;
    const concrete = universe.filter((token) => !isTennisToken(token));
    const gameGate = await Promise.all(
      concrete.map(async (key) => [key, await leagueHasGames(key)] as const),
    );
    const resolved = await resolveActiveSports(universe);
    return new Response(
      JSON.stringify({
        universe,
        autodetect: AUTODETECT_SPORTS,
        inSeasonListOk: inSeason !== null,
        allListOk: allKeys !== null,
        inSeasonCount: inSeason?.length ?? 0,
        allCount: allKeys?.length ?? 0,
        tennisKeysInSeason: (inSeason ?? []).filter((k) =>
          k.startsWith("tennis")
        ),
        tennisKeysAll: (allKeys ?? []).filter((k) => k.startsWith("tennis")),
        leagueGames: Object.fromEntries(gameGate),
        resolved,
      }),
      { headers: { "Content-Type": "application/json" } },
    );
  }

  const slot = currentSlot();
  const startedAt = new Date().toISOString();
  const desiredSports = (!AUTODETECT_SPORTS && MANUAL_SPORTS.length > 0)
    ? MANUAL_SPORTS
    : SPORT_UNIVERSE;
  const activeSports = await resolveActiveSports(desiredSports);

  let sportsToRun = activeSports;
  const proximityBySport: Record<string, number | null> = {};
  if (PROXIMITY_THROTTLE && !forceRun) {
    const due: string[] = [];
    for (let i = 0; i < activeSports.length; i++) {
      const sportKey = activeSports[i];
      const hours = await hoursUntilNearestGame(sportKey);
      proximityBySport[sportKey] = hours;
      if (sportDueThisSlot(hours, slot, i, PROXIMITY_CONFIG)) due.push(sportKey);
    }
    sportsToRun = due;
  }

  if (sportsToRun.length === 0) {
    await supabase.from("odds_ingest_runs").insert({
      status: "skipped",
      sports_requested: activeSports,
      rotation_slot: slot,
      started_at: startedAt,
      finished_at: new Date().toISOString(),
      credits_used: 0,
      api_requests: 0,
    });
    return new Response(
      JSON.stringify({
        status: "skipped",
        slot,
        reason: "no sport due this slot (proximity throttle)",
        proximity: proximityBySport,
      }),
      { headers: { "Content-Type": "application/json" } },
    );
  }

  let runId: string | undefined;
  const runInsert = await supabase.from("odds_ingest_runs").insert({
    status: "running",
    sports_requested: sportsToRun,
    rotation_slot: slot,
    started_at: startedAt,
  }).select("id").single();
  if (!runInsert.error) runId = runInsert.data.id;

  let fixtures = 0;
  let oddsRows = 0;
  let creditsUsed = 0;
  let apiRequests = 0;
  let remaining: number | null = null;

  try {
    const jobs = buildJobs(slot, sportsToRun);
    let enrichedEvents = 0;

    const forceFull = forceRun;
    const effMaxCredits = forceFull
      ? Number(Deno.env.get("ODDS_FORCE_MAX_CREDITS_PER_RUN") ?? "500")
      : MAX_CREDITS_PER_RUN;
    const effMaxEventsPerEnrich = forceFull
      ? Number.MAX_SAFE_INTEGER
      : MAX_EVENTS_PER_ENRICH;

    for (const job of jobs) {
      const firstJob = apiRequests === 0;
      if (!firstJob && creditsUsed + job.estimatedCredits > effMaxCredits) {
        continue;
      }

      if (job.kind === "main") {
        const { events, creditsUsed: cost, remaining: rem } = await fetchMain(job);
        apiRequests += 1;
        creditsUsed += cost;
        if (rem !== null) remaining = rem;
        fixtures += await upsertFixtures(job.sportKey, events);
        oddsRows += await upsertOdds(job.sportKey, events);
      } else {
        if (!forceFull && enrichedEvents >= MAX_EVENTS_PER_ENRICH * Math.max(sportsToRun.length, 1)) continue;
        const fixtureIds = await getUpcomingFixtureIds(job.sportKey);
        if (!fixtureIds.length) continue;
        const rotated = rotate(fixtureIds, slot).slice(0, effMaxEventsPerEnrich);
        const pairCredits = job.perEventCredits ?? job.estimatedCredits;
        for (const eventId of rotated) {
          const firstEnrich = apiRequests === 0;
          if (!firstEnrich && creditsUsed + pairCredits > effMaxCredits) break;
          const sharp = await fetchEventOdds(
            job.sportKey,
            eventId,
            job.markets,
            job.sharpRegions ?? ENRICH_SHARP_REGIONS,
            job.sharpBookmakers ?? SHARP_BOOK,
          );
          apiRequests += 1;
          creditsUsed += sharp.creditsUsed;
          if (sharp.remaining !== null) remaining = sharp.remaining;
          if (sharp.event) oddsRows += await upsertOdds(job.sportKey, [sharp.event]);
          const soft = await fetchEventOdds(
            job.sportKey,
            eventId,
            job.markets,
            job.softRegions ?? ENRICH_REGIONS,
            job.softBookmakers ?? SOFT_BOOKMAKERS,
          );
          apiRequests += 1;
          creditsUsed += soft.creditsUsed;
          if (soft.remaining !== null) remaining = soft.remaining;
          if (soft.event) oddsRows += await upsertOdds(job.sportKey, [soft.event]);
          enrichedEvents += 1;
        }
      }
    }

    if (runId) {
      await supabase.from("odds_ingest_runs").update({
        status: "ok",
        fixtures_upserted: fixtures,
        odds_rows_upserted: oddsRows,
        credits_used: creditsUsed,
        credits_remaining: remaining,
        api_requests: apiRequests,
        finished_at: new Date().toISOString(),
      }).eq("id", runId);
    }

    return new Response(
      JSON.stringify({ status: "ok", slot, fixtures, oddsRows, creditsUsed, remaining, apiRequests }),
      { headers: { "Content-Type": "application/json" } },
    );
  } catch (error) {
    if (runId) {
      await supabase.from("odds_ingest_runs").update({
        status: "failed",
        fixtures_upserted: fixtures,
        odds_rows_upserted: oddsRows,
        credits_used: creditsUsed,
        credits_remaining: remaining,
        api_requests: apiRequests,
        error: error instanceof Error ? error.message : String(error),
        finished_at: new Date().toISOString(),
      }).eq("id", runId);
    }
    return new Response(JSON.stringify({ status: "failed", error: String(error) }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
});
