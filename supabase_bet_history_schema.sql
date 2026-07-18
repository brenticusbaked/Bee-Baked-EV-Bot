-- Personal betting-history store used for realized-performance calibration.
-- Safe to re-run: all statements use IF NOT EXISTS.
--
-- This table holds the user's own settled/pending wagers imported from their
-- sportsbook transaction export. It is used ONLY to calibrate execution-side
-- weighting (book quality, EV floor, CLV baseline). It never feeds fair-value
-- probabilities; Pinnacle remains the sole fair-market baseline.

create table if not exists bet_history (
    bet_id                text primary key,
    book                  text,
    raw_book              text,
    bet_type              text,
    status                text,
    result                text,
    odds_decimal          double precision,
    closing_line_decimal  double precision,
    predicted_ev          double precision,
    stake                 double precision,
    profit                double precision,
    placed_at             timestamptz,
    settled_at            timestamptz,
    sport                 text,
    league                text,
    tags                  text[],
    imported_at           timestamptz not null default now()
);

create index if not exists bet_history_book_idx on bet_history (book);
create index if not exists bet_history_result_idx on bet_history (result);
create index if not exists bet_history_placed_at_idx on bet_history (placed_at);

-- Compact aggregate summary (book ROI, EV-bucket ROI, CLV baselines) that the
-- runtime engine reads to calibrate weighting/thresholds without scanning the
-- full history table on every run. A single 'latest' row is kept.
create table if not exists bet_history_summary (
    id          text primary key default 'latest',
    summary     jsonb not null,
    updated_at  timestamptz not null default now()
);
