import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";
import { type ProximityConfig, sportDueThisSlot } from "./throttle.ts";
import {
  hasGameWithinHorizon,
  isTennisToken,
  resolveSportsFromActive,
} from "./sports.ts";

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
  // Enrich jobs carry BOTH the sharp (Pinnacle/EU) and soft (US) pull as an
  // atomic pair. They remain two separate API requests (Rule 2 — sharp/soft
  // isolation), but are budgeted together so a sharp pull is never paid for
  // without its soft counterpart. Undefined for main jobs.
  sharpRegions?: string;
  sharpBookmakers?: string;
  softRegions?: string;
  softBookmakers?: string;
  perEventCredits?: number;
};

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
// Tiered key pool in strict priority order: ODDS_API_KEY is the 20k-credit
// primary that carries the whole budget; ODDS_API_KEY_2..4 are 500-credit/mo
// reserves used ONLY as failover on quota/auth errors (401/402/429). Keys are
// tried in this fixed order (never round-robin) so the small reserve keys are
// not burned prematurely and a single exhausted key never stalls ingestion.
const ODDS_API_KEYS = [
  Deno.env.get("ODDS_API_KEY") ?? "",
  Deno.env.get("ODDS_API_KEY_2") ?? "",
  Deno.env.get("ODDS_API_KEY_3") ?? "",
  Deno.env.get("ODDS_API_KEY_4") ?? "",
].map((key) => key.trim()).filter(Boolean);
// Trimmed so a stray trailing space/newline in the stored secret (a common
// cause of spurious 401s) can't break the exact-match auth check below.
const INGEST_SECRET = (Deno.env.get("ODDS_INGEST_FUNCTION_SECRET") ?? "").trim();
const REGIONS = Deno.env.get("ODDS_API_REGIONS") ?? "us,eu";
// Target books. The Odds API bills per REGION (or per 10 bookmakers), not per
// individual book, so widening this list up to 10 books adds ZERO credit cost
// while giving props a broader consensus baseline and more soft-book mispricings
// to catch. Keep pinnacle (eu, sharp baseline for main markets) first and keep
// the list at <=10 so it stays a single region-equivalent. Books past 10 would
// bill as an extra region-equivalent.
const BOOKMAKERS = Deno.env.get("ODDS_API_TARGET_BOOKS") ??
  "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,bovada,espnbet,fanatics,betrivers";
const MAIN_MARKETS = Deno.env.get("ODDS_API_MARKETS") ?? "h2h,spreads,totals";
const WNBA_PROP_MARKETS = Deno.env.get("ODDS_API_WNBA_PROP_MARKETS") ??
  "player_points,player_rebounds,player_assists,player_threes,player_blocks,player_steals,player_turnovers,player_points_rebounds_assists,player_points_rebounds,player_points_assists,player_rebounds_assists,player_field_goals,player_frees_made,player_frees_attempts";
const NFL_PROP_MARKETS = Deno.env.get("ODDS_API_NFL_PROP_MARKETS") ??
  "player_pass_attempts,player_pass_completions,player_pass_interceptions,player_pass_longest_completion,player_pass_rush_yds,player_pass_rush_reception_tds,player_pass_rush_reception_yds,player_pass_tds,player_pass_yds,player_pass_yds_q1,player_pats,player_receptions,player_reception_longest,player_reception_tds,player_reception_yds,player_rush_attempts,player_rush_longest,player_rush_reception_tds,player_rush_reception_yds,player_rush_tds,player_rush_yds,player_sacks,player_solo_tackles,player_tackles_assists,player_kicking_points,player_field_goals,player_defensive_interceptions";
// Tennis is priced on the moneyline (h2h) only — game spreads/totals are thin
// and less reliably posted by Pinnacle, and h2h keeps each tournament's main
// pull cheap (1 market x regions). Override per-sport main markets here.
const TENNIS_MAIN_MARKETS = Deno.env.get("ODDS_API_TENNIS_MARKETS") ?? "h2h";
function mainMarketsFor(sportKey: string): string {
  return sportKey.startsWith("tennis") ? TENNIS_MAIN_MARKETS : MAIN_MARKETS;
}
// Soft (recreational) enrichment pull: US region, rec books only.
const ENRICH_REGIONS = Deno.env.get("ODDS_API_ENRICH_REGIONS") ?? "us";
// Sharp enrichment pull: Pinnacle lives only in `eu`, and it is the mandatory
// fair-value baseline for every prop (.windsurfrules Rule 1/2). Fetched as its
// own request so sharp and soft data stay isolated.
const SHARP_BOOK = Deno.env.get("ODDS_API_SHARP_BOOK") ?? "pinnacle";
const ENRICH_SHARP_REGIONS = Deno.env.get("ODDS_API_ENRICH_SHARP_REGIONS") ?? "eu";
// Soft books for the enrichment pull = target books minus the sharp book.
const SOFT_BOOKMAKERS = (Deno.env.get("ODDS_API_TARGET_BOOKS") ??
  "pinnacle,fanduel,draftkings,betmgm,bet365,caesars,bovada,espnbet,fanatics,betrivers")
  .split(",")
  .map((book) => book.trim())
  .filter((book) => book && book !== SHARP_BOOK)
  .join(",");

// Extended US book coverage. Bovada is already covered by the `us` region on
// every main run at no extra cost. The only extra region we pull is `us_ex`
// (US exchanges) for Novig — cost is billed per region, not per book, so Kalshi
// / Polymarket / ProphetX come along for free in the same request. Fliff / ESPN
// BET (ex-theScore) live in a separate `us2` region and are NOT pulled by
// default to avoid the extra per-region credit; add "us2" to
// ODDS_API_EXTENDED_REGIONS (and the books to ODDS_API_EXTENDED_BOOKS) to opt
// in. Extended pulls run every N slots and stay bounded by MAX_CREDITS_PER_RUN.
// Set ODDS_EXTENDED_EVERY_N_SLOTS=0 to disable extended coverage entirely.
const EXTENDED_REGIONS = Deno.env.get("ODDS_API_EXTENDED_REGIONS") ?? "us_ex";
const EXTENDED_BOOKS = Deno.env.get("ODDS_API_EXTENDED_BOOKS") ??
  "novig,kalshi,polymarket,prophetx";
// Disabled by default: the extended pull only adds the betting EXCHANGES
// (Novig/Kalshi/Polymarket/ProphetX), which are used almost exclusively for the
// arbitrage scanner. Scheduled arb alerts are off, so paying an extra billed
// region for exchange lines every few slots is wasted budget — those freed
// credits go to the sharp/soft prop pulls instead. Set
// ODDS_EXTENDED_EVERY_N_SLOTS>0 to re-enable exchange coverage for manual arb.
const EXTENDED_EVERY_N_SLOTS = Number(Deno.env.get("ODDS_EXTENDED_EVERY_N_SLOTS") ?? "0");
const ENABLE_MARKET_ENRICHMENT =
  (Deno.env.get("ENABLE_MARKET_ENRICHMENT") ?? "true").toLowerCase() !== "false";

// Strict per-run credit ceiling. Each prop group needs BOTH a sharp (Pinnacle/
// EU) and a soft (US) per-event pull, and the two are budgeted as an atomic pair
// (see the enrich executor) so a sharp pull is never paid for without its soft
// counterpart. With two in-season sports the mains cost ~12 credits (2 sports x
// 3 markets x us,eu) and one prop pair costs ~10 (5 markets x eu + 5 x us), so
// the ceiling must clear ~22 for props to land at all — 14 could only ever fit
// the sharp half, which is why soft-book props were missing. Paired with a
// ~30-runs/day cron (see supabase_edge_cron_setup.sql), 24 credits/run lands
// ~20k credits/month. Raise/lower to match your cron cadence and in-season
// sport count in ODDS_API_ACTIVE_SPORTS.
const MAX_CREDITS_PER_RUN = Number(Deno.env.get("ODDS_MAX_CREDITS_PER_RUN") ?? "24");
// Cap on how many events get per-event enrichment (props/alternates/derivatives)
// in a single run. Events rotate across runs by the time-based slot.
const MAX_EVENTS_PER_ENRICH = Number(Deno.env.get("ODDS_MAX_EVENTS_PER_ENRICH") ?? "2");
const CYCLE_MINUTES = Number(Deno.env.get("ODDS_CYCLE_MINUTES") ?? "10");

// --- Game-proximity throttle -------------------------------------------------
// Concentrate the Odds API budget on games that are close to first pitch/tip and
// spend almost nothing when the nearest game is far away. On each cron tick the
// function looks up the nearest upcoming fixture per sport, derives a target
// poll interval from how many hours out that game is, and only pulls the sport
// on ticks that land on that interval (measured in CYCLE_MINUTES slots). This
// reproduces a "poll faster as the game approaches" schedule while staying on
// the serverless pg_cron model — no long-running process, no extra infra, and
// it can only ever REDUCE spend versus pulling every sport every tick. The
// tiers mirror the syndicate's requested schedule (far → sparse ... imminent →
// every tick). Set ODDS_PROXIMITY_THROTTLE=false to pull every active sport on
// every tick (previous behaviour).
const PROXIMITY_THROTTLE =
  (Deno.env.get("ODDS_PROXIMITY_THROTTLE") ?? "true").toLowerCase() !== "false";
// Hour cutoffs (hours until nearest game) that define the tiers.
const PROXIMITY_FAR_HOURS = Number(Deno.env.get("ODDS_PROXIMITY_FAR_HOURS") ?? "24");
const PROXIMITY_MID_HOURS = Number(Deno.env.get("ODDS_PROXIMITY_MID_HOURS") ?? "12");
const PROXIMITY_NEAR_HOURS = Number(Deno.env.get("ODDS_PROXIMITY_NEAR_HOURS") ?? "2");
// Target minutes between pulls for each tier.
//   > FAR_HOURS            -> POLL_FAR_MINUTES     (default 4h)
//   MID_HOURS..FAR_HOURS   -> POLL_MID_MINUTES     (default 1h)
//   NEAR_HOURS..MID_HOURS  -> POLL_CLOSE_MINUTES   (default 15m)
//   <= NEAR_HOURS (or live)-> every tick (CYCLE_MINUTES)
const POLL_FAR_MINUTES = Number(Deno.env.get("ODDS_POLL_FAR_MINUTES") ?? "240");
const POLL_MID_MINUTES = Number(Deno.env.get("ODDS_POLL_MID_MINUTES") ?? "60");
const POLL_CLOSE_MINUTES = Number(Deno.env.get("ODDS_POLL_CLOSE_MINUTES") ?? "15");
// Nearest game unknown (no fixtures cached yet) -> moderate discovery cadence so
// a fresh slate is still picked up promptly without polling a dead sport all day.
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

// Universe of leagues the syndicate cares about, in realized-ROI priority order
// (MLB best on volume, then WNBA; NBA/NHL/NFL for their seasons; tennis).
// The first sport is favored by the first-job-always-runs rule, so keep the
// strongest market first. This is a *superset* — each run auto-narrows it to the
// leagues actually in season (see resolveActiveSports), so idle leagues cost 0
// credits and NBA/NHL/NFL switch on by themselves when their seasons open. No
// need to edit this by season. Tokens may be concrete Odds API sport keys or
// umbrella tennis tokens (`tennis`, `tennis_atp`, `tennis_wta`); tennis keys are
// per-tournament and rotate weekly, so they're expanded at runtime.
const SPORT_UNIVERSE = (Deno.env.get("ODDS_API_SPORT_UNIVERSE") ??
  "baseball_mlb,basketball_wnba,basketball_nba,icehockey_nhl,americanfootball_nfl,tennis")
  .split(",")
  .map((sport) => sport.trim())
  .filter(Boolean);

// Auto-detect in-season leagues by default. Set ODDS_API_AUTODETECT_SPORTS=false
// to pin the run to an explicit ODDS_API_ACTIVE_SPORTS list instead (manual
// override; still season-filtered and tennis-expanded).
const AUTODETECT_SPORTS =
  (Deno.env.get("ODDS_API_AUTODETECT_SPORTS") ?? "true").toLowerCase() !==
    "false";
const MANUAL_SPORTS = (Deno.env.get("ODDS_API_ACTIVE_SPORTS") ?? "")
  .split(",")
  .map((sport) => sport.trim())
  .filter(Boolean);

// How far ahead a league must have a scheduled game to count as "in season".
// Covers the current + upcoming slate (e.g. NFL/NHL preseason a week out) while
// still excluding a league that only posts distant futures.
const SEASON_HORIZON_HOURS = Number(
  Deno.env.get("ODDS_SEASON_HORIZON_HOURS") ?? "192",
);

// True if a concrete league has at least one real, dated game within the season
// horizon. Uses the FREE /v4/sports/{key}/events endpoint (0 credits) which
// lists only actual game events — never futures/outrights — so a league that is
// technically "active" year-round for Super Bowl/Stanley Cup futures does NOT
// count as in season here. On any error we return false (exclude) rather than
// spend main-pull credits on a league we can't confirm has games.
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

// Resolve the desired league universe into the concrete sport keys to pull this
// run. Two independent free (0-credit) signals:
//   - Concrete leagues (baseball_mlb, basketball_nba, ...): kept only if they
//     have a real upcoming GAME (leagueHasGames -> /events). This is what makes
//     NBA/NHL/NFL switch on exactly when their seasons have games and stay off
//     — with zero wasted credits — the rest of the year.
//   - Tennis umbrella tokens: expanded to the currently-active per-tournament
//     keys from the /v4/sports listing (keys rotate weekly).
// Free /events checks run in parallel. If everything is unreachable the run
// simply yields no sports (a skipped tick), which is cheaper and safer than
// blindly pulling every league.
// Raw /v4/sports listing (free, 0 credits). Returns the sport keys, or null if
// the listing couldn't be fetched/parsed. `all=true` includes sports that are
// out of season / not in the default in-season listing — needed for tennis,
// whose per-tournament keys are frequently absent from the default listing even
// while tournaments are running. Kept separate so a debug call can surface
// exactly what The Odds API is returning.
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

  // Tennis discovery. Use the `all=true` listing because tennis tournaments are
  // routinely missing from the default in-season listing, then keep only the
  // tennis keys that actually have upcoming matches (leagueHasGames -> /events),
  // exactly like the concrete leagues. This avoids requesting a listed-but-empty
  // tournament and burns 0 credits (both endpoints are free).
  let tennisCandidates: string[] = [];
  if (wantsTennis) {
    const allKeys = (await fetchSportKeys(true)) ?? [];
    const tennisKeys = allKeys.filter((key) => key.startsWith("tennis"));
    // Expand umbrella tokens (tennis / tennis_atp / tennis_wta) to candidates.
    tennisCandidates = resolveSportsFromActive(
      universe.filter(isTennisToken),
      tennisKeys,
    );
  }

  // Gate concrete leagues AND tennis candidates on real upcoming games.
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

// Per-sport expansion markets, fetched from the per-event odds endpoint. Each
// entry is an ordered list of market GROUPS; every group becomes its own enrich
// job. Groups are kept small (<=5 markets => <=5 credits/event per region) so
// each fits under MAX_CREDITS_PER_RUN, and the rotation fans the groups out
// across cron slots. Order matters: earlier groups (highest-liquidity, main
// lines) are favored by the rotation.
//
// IMPORTANT: the +EV engine prices every player prop against the PINNACLE
// baseline (multiplicative de-vig; see unified_bot.evaluate_player_props and
// .windsurfrules Rule 1). It can only alert on markets Pinnacle actually posts
// as clean Over/Under pairs. So these lists are limited to standard counting-
// stat Over/Under props + team alternate spread/total ladders + quarter/half
// derivatives. NOT included (Pinnacle doesn't post them / they aren't clean
// two-way markets, so the current engine can't price them and pulling them
// would waste credits): alternate player-prop ladders, first-basket, anytime-
// scorer, double/triple-double, record-a-win. Pricing those needs a
// distribution model off the Pinnacle main line (utils/prop_pricing.py) wired
// into the alert path — a separate, approval-gated change.
type SportExtras = { groups: string[] };
const SPORT_EXTRAS: Record<string, SportExtras> = {
  baseball_mlb: {
    groups: [
      "pitcher_strikeouts,pitcher_outs,pitcher_hits_allowed,pitcher_earned_runs",
      "batter_hits,batter_total_bases,batter_home_runs,batter_rbis,batter_runs_scored",
      "batter_strikeouts,batter_walks,batter_stolen_bases",
      "alternate_spreads,alternate_totals",
    ],
  },
  basketball_wnba: {
    groups: [
      WNBA_PROP_MARKETS,
      "alternate_spreads,alternate_totals",
      "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    ],
  },
  basketball_nba: {
    groups: [
      "player_points,player_rebounds,player_assists,player_threes,player_points_rebounds_assists",
      "player_points_rebounds,player_points_assists,player_rebounds_assists,player_blocks,player_steals",
      "player_turnovers",
      "alternate_spreads,alternate_totals",
      "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    ],
  },
  americanfootball_nfl: {
    groups: [
      NFL_PROP_MARKETS,
      "alternate_spreads,alternate_totals",
      "h2h_q1,spreads_q1,h2h_h1,spreads_h1",
    ],
  },
  icehockey_nhl: {
    groups: [
      "player_points,player_goals,player_assists,player_shots_on_goal,player_total_saves",
      "player_blocked_shots,player_power_play_points",
      "alternate_spreads,alternate_totals",
    ],
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

// Hours until the nearest upcoming (or currently-live) game for a sport, read
// from cached fixtures. Costs zero Odds API credits. Returns null when no
// fixture is cached in range so the caller falls back to the discovery cadence.
async function hoursUntilNearestGame(sportKey: string): Promise<number | null> {
  const now = Date.now();
  // Include games that started up to ~3h ago so in-play events still count as
  // "near". Horizon of 72h keeps far-out openers visible for the sparse tier.
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

  // Extended-book main pull (Fliff / ESPN BET / exchanges) in their own regions.
  // Runs only every EXTENDED_EVERY_N_SLOTS-th slot so the extra billed regions
  // don't refresh every cycle; the per-run ceiling still bounds total spend.
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

  // Enrichment fetches derivatives / alternates / player props from the
  // per-event odds endpoint. Every prop is priced against the PINNACLE baseline
  // downstream, and Pinnacle only lives in the `eu` region, so each group needs
  // BOTH a sharp pull (eu, pinnacle only) and a soft pull (us, rec books). These
  // are kept as separate requests per .windsurfrules Rule 2 (sharp/soft data
  // isolation); the cache merges them back per fixture. Without the sharp pull,
  // props have no baseline and never alert.
  const enrichJobs: IngestJob[] = [];
  if (ENABLE_MARKET_ENRICHMENT) {
    for (const sportKey of sports) {
      const extras = SPORT_EXTRAS[sportKey];
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

  // Rotate each group independently by the time slot so main refreshes, extended
  // book pulls, and expensive enrichment pulls fan out fairly across the day.
  return [
    ...rotate(mainJobs, slot),
    ...rotate(extendedJobs, slot),
    ...rotate(enrichJobs, slot),
  ];
}

// Fetch with tiered-key failover in strict priority order (primary 20k key
// first, then 500-credit reserves). Advances to the next key only on
// quota/auth errors so one exhausted key never stalls the run.
async function oddsFetch(url: URL): Promise<Response> {
  let lastResponse: Response | null = null;
  for (const key of ODDS_API_KEYS) {
    url.searchParams.set("apiKey", key);
    const response = await fetch(url);
    if (![401, 402, 429].includes(response.status)) {
      return response;
    }
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
  if (INGEST_SECRET && (request.headers.get("x-ingest-secret") ?? "").trim() !== INGEST_SECRET) {
    return new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 });
  }
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || ODDS_API_KEYS.length === 0) {
    return new Response(JSON.stringify({ error: "missing required environment variables" }), { status: 500 });
  }

  // Manual/forced invocations bypass the proximity throttle so a hand-fired
  // test (or an ad-hoc backfill) always pulls every active sport instead of
  // possibly landing on a "skipped" slot. Triggered by `{"force": true}` or any
  // trigger string starting with "manual" in the request body.
  let forceRun = false;
  let debug = "";
  try {
    const body = await request.json();
    const trigger = String((body as { trigger?: unknown })?.trigger ?? "");
    forceRun = (body as { force?: unknown })?.force === true ||
      trigger.startsWith("manual");
    debug = String((body as { debug?: unknown })?.debug ?? "");
  } catch (_error) {
    // No/invalid JSON body — treat as a normal scheduled tick.
  }

  // Zero-credit diagnostic: `{"debug":"sports"}` returns exactly what the
  // sport-resolution sees (raw active listing, tennis keys, per-league game
  // gating, final resolved set) without pulling any odds. Use it to see why a
  // league/tennis is or isn't being requested.
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
  // Narrow the desired league set to what's in season (and expand tennis to its
  // active tournament keys). Default: auto-detect from the full universe. If
  // auto-detect is disabled and an explicit ODDS_API_ACTIVE_SPORTS is set, use
  // that as the desired set instead.
  const desiredSports = (!AUTODETECT_SPORTS && MANUAL_SPORTS.length > 0)
    ? MANUAL_SPORTS
    : SPORT_UNIVERSE;
  const activeSports = await resolveActiveSports(desiredSports);

  // Game-proximity throttle: only pull sports whose nearest game makes them due
  // on this tick. Spends nothing on sports whose next game is hours away.
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

  // Nothing due this tick — record a zero-credit skipped run (so the schedule is
  // visibly ticking, not silently dead) and return without spending credits.
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
        if (enrichedEvents >= MAX_EVENTS_PER_ENRICH * Math.max(sportsToRun.length, 1)) continue;
        const fixtureIds = await getUpcomingFixtureIds(job.sportKey);
        if (!fixtureIds.length) continue;
        const rotated = rotate(fixtureIds, slot).slice(0, MAX_EVENTS_PER_ENRICH);
        const pairCredits = job.perEventCredits ?? job.estimatedCredits;
        for (const eventId of rotated) {
          const firstEnrich = apiRequests === 0;
          // Reserve the FULL sharp+soft pair budget before starting so we never
          // pay for a sharp pull that can't be de-vigged with a soft price into
          // an alert (the bug that left soft-book props missing).
          if (!firstEnrich && creditsUsed + pairCredits > MAX_CREDITS_PER_RUN) break;
          // Sharp pull (Pinnacle/EU) — mandatory fair-value baseline.
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
          // Soft pull (US rec books) — separate request (Rule 2), same event.
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
