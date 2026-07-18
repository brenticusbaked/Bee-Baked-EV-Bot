import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

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
  estimatedCredits: number;
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const ODDS_API_KEY = Deno.env.get("ODDS_API_KEY") ?? "";
const INGEST_SECRET = Deno.env.get("ODDS_INGEST_FUNCTION_SECRET") ?? "";
const REGIONS = Deno.env.get("ODDS_API_REGIONS") ?? "us,eu";
const BOOKMAKERS = Deno.env.get("ODDS_API_TARGET_BOOKS") ??
  "pinnacle,fanduel,draftkings,betmgm,bet365,caesars";
const MAIN_MARKETS = Deno.env.get("ODDS_API_MARKETS") ?? "h2h,spreads,totals";
const ENRICH_REGIONS = Deno.env.get("ODDS_API_ENRICH_REGIONS") ?? "us";
const ENABLE_MARKET_ENRICHMENT =
  (Deno.env.get("ENABLE_MARKET_ENRICHMENT") ?? "true").toLowerCase() !== "false";

// Strict per-run credit ceiling. At 144 runs/day this multiplies out to the
// monthly budget: 5 credits/run * 144 = 720/day ~= 20,000/month after
// in-season filtering trims idle sports. The executor always runs the first
// queued job so the rotation keeps making progress even if one job alone
// exceeds the ceiling.
const MAX_CREDITS_PER_RUN = Number(Deno.env.get("ODDS_MAX_CREDITS_PER_RUN") ?? "5");
// Cap on how many events get per-event enrichment (props/alternates/derivatives)
// in a single run. Events rotate across runs by the time-based slot.
const MAX_EVENTS_PER_ENRICH = Number(Deno.env.get("ODDS_MAX_EVENTS_PER_ENRICH") ?? "2");
const CYCLE_MINUTES = Number(Deno.env.get("ODDS_CYCLE_MINUTES") ?? "10");

const ACTIVE_SPORTS = (Deno.env.get("ODDS_API_ACTIVE_SPORTS") ??
  "basketball_nba,basketball_wnba,baseball_mlb,icehockey_nhl")
  .split(",")
  .map((sport) => sport.trim())
  .filter(Boolean);

// Per-sport expansion markets, fetched from the per-event odds endpoint.
// Derivatives = 1st quarter / 1st half lines (lower-limit inefficiencies).
// Alternates = alternate spread/total ladders.
// Props = player props with asymmetric juice (de-vigged multiplicatively downstream).
type SportExtras = { derivatives?: string; alternates?: string; props?: string };
const SPORT_EXTRAS: Record<string, SportExtras> = {
  basketball_nba: {
    derivatives: "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    alternates: "alternate_spreads,alternate_totals",
    props: "player_points,player_rebounds,player_assists",
  },
  basketball_wnba: {
    derivatives: "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    alternates: "alternate_spreads,alternate_totals",
  },
  icehockey_nhl: {
    alternates: "alternate_spreads,alternate_totals",
    props: "player_shots_on_goal",
  },
  baseball_mlb: {
    alternates: "alternate_spreads,alternate_totals",
  },
};

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

async function getUpcomingFixtureIds(sportKey: string): Promise<string[]> {
  // Live and near-term fixtures only (avoids paying to enrich finished games).
  const lookbackHours = 6;
  const horizonHours = 30;
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

function buildJobs(slot: number): IngestJob[] {
  const mainJobs: IngestJob[] = ACTIVE_SPORTS.map((sportKey) => ({
    kind: "main",
    sportKey,
    markets: MAIN_MARKETS,
    regions: REGIONS,
    estimatedCredits: creditsFor(MAIN_MARKETS, REGIONS),
  }));

  const enrichJobs: IngestJob[] = [];
  if (ENABLE_MARKET_ENRICHMENT) {
    for (const sportKey of ACTIVE_SPORTS) {
      const extras = SPORT_EXTRAS[sportKey];
      if (!extras) continue;
      for (const group of [extras.derivatives, extras.alternates, extras.props]) {
        if (!group) continue;
        enrichJobs.push({
          kind: "enrich",
          sportKey,
          markets: group,
          regions: ENRICH_REGIONS,
          estimatedCredits: creditsFor(group, ENRICH_REGIONS),
        });
      }
    }
  }

  // Rotate each group independently by the time slot so both main refreshes
  // and expensive enrichment pulls fan out fairly across the day.
  return [...rotate(mainJobs, slot), ...rotate(enrichJobs, slot)];
}

async function fetchMain(job: IngestJob): Promise<{ events: OddsEvent[]; creditsUsed: number; remaining: number | null }> {
  const url = new URL(`https://api.the-odds-api.com/v4/sports/${job.sportKey}/odds`);
  url.searchParams.set("apiKey", ODDS_API_KEY);
  url.searchParams.set("regions", job.regions);
  url.searchParams.set("markets", job.markets);
  url.searchParams.set("bookmakers", BOOKMAKERS);
  url.searchParams.set("oddsFormat", "decimal");

  const response = await fetch(url);
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
): Promise<{ event: OddsEvent | null; creditsUsed: number; remaining: number | null }> {
  const url = new URL(`https://api.the-odds-api.com/v4/sports/${sportKey}/events/${eventId}/odds`);
  url.searchParams.set("apiKey", ODDS_API_KEY);
  url.searchParams.set("regions", regions);
  url.searchParams.set("markets", markets);
  url.searchParams.set("bookmakers", BOOKMAKERS);
  url.searchParams.set("oddsFormat", "decimal");

  const response = await fetch(url);
  const remaining = numericHeader(response, "x-requests-remaining");
  const lastCost = numericHeader(response, "x-requests-last");
  if (response.status === 404 || response.status === 422) {
    // Market not offered for this event/sport — treat as a no-op, still billed.
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

// Cache invalidation: latest stored last_update per fixture|book|market so we
// only write rows when the API payload is genuinely newer.
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
        // Skip stale markets: only ingest when strictly newer than what we have.
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
  if (INGEST_SECRET && request.headers.get("x-ingest-secret") !== INGEST_SECRET) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  }
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !ODDS_API_KEY) {
    return new Response(JSON.stringify({ error: "missing required environment variables" }), { status: 500 });
  }

  const slot = currentSlot();
  const startedAt = new Date().toISOString();
  let runId: string | undefined;
  const runInsert = await supabase.from("odds_ingest_runs").insert({
    status: "running",
    sports_requested: ACTIVE_SPORTS,
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
    const jobs = buildJobs(slot);
    let enrichedEvents = 0;

    for (const job of jobs) {
      const firstJob = apiRequests === 0;
      if (!firstJob && creditsUsed + job.estimatedCredits > MAX_CREDITS_PER_RUN) {
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
        if (enrichedEvents >= MAX_EVENTS_PER_ENRICH * ACTIVE_SPORTS.length) continue;
        const fixtureIds = await getUpcomingFixtureIds(job.sportKey);
        if (!fixtureIds.length) continue;
        const rotated = rotate(fixtureIds, slot).slice(0, MAX_EVENTS_PER_ENRICH);
        for (const eventId of rotated) {
          const firstEnrich = apiRequests === 0;
          if (!firstEnrich && creditsUsed + job.estimatedCredits > MAX_CREDITS_PER_RUN) break;
          const { event, creditsUsed: cost, remaining: rem } = await fetchEventOdds(
            job.sportKey,
            eventId,
            job.markets,
            job.regions,
          );
          apiRequests += 1;
          creditsUsed += cost;
          enrichedEvents += 1;
          if (rem !== null) remaining = rem;
          if (event) oddsRows += await upsertOdds(job.sportKey, [event]);
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
