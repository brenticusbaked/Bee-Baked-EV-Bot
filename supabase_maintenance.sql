-- ============================================================
-- Supabase maintenance: report on and reclaim database size.
--
-- Run these in the Supabase SQL editor, in order. Section 1 is read-only.
--
-- Why the project grows: `historical_odds.line_hash` includes the price, so
-- every price change on every outcome of every book is a new row that is never
-- removed, and both `fixtures.raw_event` and `historical_odds.raw_outcome`
-- stored a full JSON copy of data their own columns already hold. Nothing in the
-- codebase reads either blob; the ingesters no longer write them.
--
-- Deleting rows does NOT return disk to Supabase on its own — PostgreSQL keeps
-- the space for future rows. Section 4's VACUUM FULL is what shrinks the files,
-- and it needs free disk roughly equal to the size of the table it rewrites.
-- ============================================================


-- ------------------------------------------------------------
-- 1. Report: where the space actually is (read-only).
-- ------------------------------------------------------------
select
    relname as table_name,
    pg_size_pretty(pg_total_relation_size(c.oid)) as total,
    pg_size_pretty(pg_relation_size(c.oid)) as heap,
    pg_size_pretty(pg_indexes_size(c.oid)) as indexes,
    pg_size_pretty(pg_total_relation_size(reltoastrelid)) as toast_jsonb,
    n_live_tup as live_rows,
    n_dead_tup as dead_rows
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
left join pg_stat_user_tables s on s.relid = c.oid
where n.nspname = 'public' and c.relkind = 'r'
order by pg_total_relation_size(c.oid) desc
limit 25;

-- Whole-database total, for comparison against the 1 GB target.
select pg_size_pretty(pg_database_size(current_database())) as database_size;


-- ------------------------------------------------------------
-- 2. Drop the write-only JSON blobs.
--    These are the TOAST-heavy columns. Emptying them first makes the
--    VACUUM FULL in section 4 much cheaper.
-- ------------------------------------------------------------
update fixtures set raw_event = '{}'::jsonb where raw_event <> '{}'::jsonb;
update historical_odds set raw_outcome = '{}'::jsonb where raw_outcome <> '{}'::jsonb;

-- Optional, once you are satisfied nothing needs them: dropping the columns
-- removes the TOAST tables outright rather than leaving empty ones behind.
-- alter table fixtures drop column raw_event;
-- alter table historical_odds drop column raw_outcome;


-- ------------------------------------------------------------
-- 3. Retention. `services/db_retention.py` does this on a schedule; these are
--    the same cuts as one-time SQL, which is far faster for a first big prune.
--    Deleting a fixture cascades to its historical_odds rows.
-- ------------------------------------------------------------
delete from historical_odds where captured_at < now() - interval '45 days';
delete from fixtures where commence_time < now() - interval '45 days';
delete from odds_ingest_runs where started_at < now() - interval '14 days';
delete from alerts_sent where sent_at < now() - interval '30 days';
delete from workflow_runs where run_at < now() - interval '30 days';
delete from venue_metrics where measured_at < now() - interval '90 days';

-- Player logs feed last-10 and season form; two seasons is plenty.
delete from mlb_player_logs where game_date < now() - interval '730 days';
delete from nba_player_logs where game_date < now() - interval '730 days';
delete from wnba_player_logs where game_date < now() - interval '730 days';
delete from nfl_player_logs where game_date < now() - interval '730 days';
delete from soccer_player_logs where game_date < now() - interval '730 days';
delete from tennis_match_logs where game_date < now() - interval '730 days';

-- bets_log, execution_* and bet_history are deliberately NOT pruned: they are
-- the ROI/CLV record and are small relative to the odds tables.


-- ------------------------------------------------------------
-- 4. Reclaim the disk. Run one statement at a time; each rewrites the table and
--    takes an exclusive lock, so do it while no pipeline run is in flight.
-- ------------------------------------------------------------
vacuum full analyze historical_odds;
vacuum full analyze fixtures;
-- odds_cache and bot_state are single-row-per-key tables that are upserted every
-- run, so their dead tuples and TOAST chunks bloat far beyond their live size.
vacuum full analyze odds_cache;
vacuum full analyze bot_state;
vacuum full analyze alerts_sent;
vacuum full analyze workflow_runs;


-- ------------------------------------------------------------
-- 5. Stop the bloat coming back on the highest-churn tables.
--    `replica identity full` writes every column of every row into the WAL on
--    each change, and realtime replication holds that WAL. Neither table has a
--    realtime subscriber in this codebase.
-- ------------------------------------------------------------
alter table historical_odds replica identity default;
alter table fixtures replica identity default;
-- alter publication supabase_realtime drop table historical_odds;
-- alter publication supabase_realtime drop table fixtures;

-- Autovacuum defaults scale with table size, which is too lazy for a table
-- taking thousands of inserts an hour.
alter table historical_odds set (autovacuum_vacuum_scale_factor = 0.02);
alter table odds_cache set (autovacuum_vacuum_scale_factor = 0.0, autovacuum_vacuum_threshold = 50);
alter table bot_state set (autovacuum_vacuum_scale_factor = 0.0, autovacuum_vacuum_threshold = 50);


-- ------------------------------------------------------------
-- 6. Re-run section 1 to confirm the result.
-- ------------------------------------------------------------
