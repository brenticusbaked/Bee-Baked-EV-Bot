alter table bets_log add column if not exists edge_pct numeric;
alter table bets_log add column if not exists odds_decimal numeric;
alter table bets_log add column if not exists fair_price_decimal numeric;
alter table bets_log add column if not exists bet_source text;
alter table bets_log add column if not exists market_type text;
alter table bets_log add column if not exists notes text;
alter table bets_log add column if not exists closing_line_american text;
alter table bets_log add column if not exists closing_line_decimal numeric;
alter table bets_log add column if not exists clv_edge_pct numeric;
alter table bets_log add column if not exists clv_tracked_at timestamptz;
alter table bets_log add column if not exists graded_at timestamptz;

create index if not exists idx_bets_log_date on bets_log(date);
create index if not exists idx_bets_log_event_id on bets_log(event_id);
create index if not exists idx_bets_log_market_type on bets_log(market_type);
