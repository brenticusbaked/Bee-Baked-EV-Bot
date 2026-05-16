import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

type OddsOutcome = {
  name: string;
  price: number;
  point?: number;
};

type OddsMarket = {
  key: string;
  outcomes?: OddsOutcome[];
};

type Bookmaker = {
  key: string;
  title?: string;
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

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const ODDS_API_KEY = Deno.env.get("ODDS_API_KEY") ?? "";
const INGEST_SECRET = Deno.env.get("ODDS_INGEST_FUNCTION_SECRET") ?? "";
const REGIONS = Deno.env.get("ODDS_API_REGIONS") ?? "us,eu";
const BOOKMAKERS = Deno.env.get("ODDS_API_TARGET_BOOKS") ??
  "pinnacle,fanduel,draftkings,betmgm,bet365,caesars";
const MARKETS = Deno.env.get("ODDS_API_MARKETS") ?? "h2h,spreads,totals";
const ACTIVE_SPORTS = (Deno.env.get("ODDS_API_ACTIVE_SPORTS") ??
  "basketball_nba,basketball_wnba,baseball_mlb,icehockey_nhl")
  .split(",")
  .map((sport) => sport.trim())
  .filter(Boolean);

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false },
});

function lineHash(
  event: OddsEvent,
  bookmaker: Bookmaker,
  market: OddsMarket,
  outcome: OddsOutcome,
  capturedAt: string,
): string {
  const point = outcome.point ?? "";
  return [
    capturedAt,
    event.id,
    bookmaker.key,
    market.key,
    outcome.name.toLowerCase().trim(),
    String(point),
    String(outcome.price),
  ].join("|");
}

async function fetchSport(sportKey: string): Promise<OddsEvent[]> {
  const url = new URL(`https://api.the-odds-api.com/v4/sports/${sportKey}/odds`);
  url.searchParams.set("apiKey", ODDS_API_KEY);
  url.searchParams.set("regions", REGIONS);
  url.searchParams.set("markets", MARKETS);
  url.searchParams.set("bookmakers", BOOKMAKERS);
  url.searchParams.set("oddsFormat", "decimal");

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${sportKey} odds fetch failed: ${response.status} ${await response.text()}`);
  }
  return await response.json();
}

async function upsertEvents(sportKey: string, events: OddsEvent[]) {
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

  if (fixtureRows.length) {
    const { error } = await supabase.from("fixtures").upsert(fixtureRows, { onConflict: "id" });
    if (error) throw error;
  }

  const capturedAt = new Date().toISOString();
  const oddsRows = [];
  for (const event of events) {
    for (const bookmaker of event.bookmakers ?? []) {
      for (const market of bookmaker.markets ?? []) {
        for (const outcome of market.outcomes ?? []) {
          if (typeof outcome.price !== "number") continue;
          oddsRows.push({
            fixture_id: event.id,
            sport_key: event.sport_key ?? sportKey,
            bookmaker_key: bookmaker.key,
            bookmaker_title: bookmaker.title ?? bookmaker.key,
            market_key: market.key,
            outcome_name: outcome.name,
            point: outcome.point ?? null,
            price_decimal: outcome.price,
            line_hash: lineHash(event, bookmaker, market, outcome, capturedAt),
            captured_at: capturedAt,
            raw_outcome: outcome,
          });
        }
      }
    }
  }

  if (oddsRows.length) {
    const { error } = await supabase.from("historical_odds").upsert(oddsRows, { onConflict: "line_hash" });
    if (error) throw error;
  }

  return { fixtures: fixtureRows.length, oddsRows: oddsRows.length };
}

serve(async (request) => {
  if (INGEST_SECRET && request.headers.get("x-ingest-secret") !== INGEST_SECRET) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  }
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !ODDS_API_KEY) {
    return new Response(JSON.stringify({ error: "missing required environment variables" }), { status: 500 });
  }

  const startedAt = new Date().toISOString();
  let runId: string | undefined;
  const runInsert = await supabase.from("odds_ingest_runs").insert({
    status: "running",
    sports_requested: ACTIVE_SPORTS,
    started_at: startedAt,
  }).select("id").single();
  if (!runInsert.error) runId = runInsert.data.id;

  try {
    let fixtures = 0;
    let oddsRows = 0;
    for (const sportKey of ACTIVE_SPORTS) {
      const events = await fetchSport(sportKey);
      const result = await upsertEvents(sportKey, events);
      fixtures += result.fixtures;
      oddsRows += result.oddsRows;
    }

    if (runId) {
      await supabase.from("odds_ingest_runs").update({
        status: "ok",
        fixtures_upserted: fixtures,
        odds_rows_upserted: oddsRows,
        finished_at: new Date().toISOString(),
      }).eq("id", runId);
    }

    return new Response(JSON.stringify({ status: "ok", fixtures, oddsRows }), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    if (runId) {
      await supabase.from("odds_ingest_runs").update({
        status: "failed",
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
