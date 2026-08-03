from dataclasses import dataclass
from typing import Callable, List

from utils.config import env_flag
from utils.seasons import is_sport_in_season


TaskFunc = Callable[[], None]


@dataclass(frozen=True)
class PipelineTask:
    name: str
    func: TaskFunc


def _hydrate_or_fetch() -> dict:
    """Hydrate from Supabase first, then fall back to The Odds API if cache is empty or stale.

    When ``ODDS_REFRESH_ON_MAIN`` is enabled, the main run pulls fresh odds up to
    ``ODDS_MAX_CREDITS_PER_RUN`` before falling back to the cache.
    """
    if env_flag("ODDS_REFRESH_ON_MAIN", True):
        print("ODDS_REFRESH_ON_MAIN enabled; running capped odds refresh.")
        from master_odds_fetcher import run_fetcher

        fetched = run_fetcher()
        if fetched.get("count"):
            return fetched
        print("Capped odds refresh returned no data; falling back to Supabase cache.")

    from datetime import datetime, timezone
    from db_manager import hydrate_market_cache, get_master_cache
    from utils.scratch_guard import safe_parse_commence_time, SCHEDULE_GRACE_MINUTES

    result = hydrate_market_cache()
    odds_count = result.get("meta", {}).get("odds_count", 0)
    event_count = result.get("count", 0)
    if event_count == 0 or odds_count == 0:
        print("Supabase cache lacks fixtures or sharp odds. Falling back to The Odds API fetcher.")
        from master_odds_fetcher import run_fetcher
        return run_fetcher()

    cache = get_master_cache() or {}
    now = datetime.now(timezone.utc)
    grace_seconds = float(SCHEDULE_GRACE_MINUTES) * 60.0
    has_fresh = False
    for events in cache.values():
        for event in events or []:
            start = safe_parse_commence_time(event.get("commence_time"))
            if start and (now - start).total_seconds() <= grace_seconds:
                has_fresh = True
                break
        if has_fresh:
            break

    if not has_fresh:
        print("Supabase cache contains no upcoming or recent events. Falling back to The Odds API fetcher.")
        from master_odds_fetcher import run_fetcher
        return run_fetcher()

    return result


def get_refresh_tasks() -> List[PipelineTask]:
    if env_flag("ENABLE_SUPABASE_FIRST_INGESTION", True):
        tasks: List[PipelineTask] = []
        if env_flag("ENABLE_INGEST_TRIGGER", False):
            from services.ingest_trigger import trigger_odds_ingest

            tasks.append(PipelineTask(name="trigger_odds_ingest", func=trigger_odds_ingest))
        tasks.append(PipelineTask(name="hydrate_market_cache", func=_hydrate_or_fetch))
        return tasks

    from master_odds_fetcher import run_fetcher

    return [PipelineTask(name="master_odds_fetcher", func=run_fetcher)]


def get_parallel_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []

    if env_flag("ENABLE_NEWS", True):
        from scraper_bot import scrape_news

        tasks.append(PipelineTask(name="injury_news", func=scrape_news))
    if env_flag("ENABLE_PLAYER_PROP_BOT", env_flag("ENABLE_NBA_PROP_BOT", True)):
        active_prop_sports = []
        for sport in ("basketball_nba", "baseball_mlb"):
            if is_sport_in_season(sport):
                active_prop_sports.append(sport)
        if env_flag("ENABLE_WNBA_PROP_BOT", False) and is_sport_in_season("basketball_wnba"):
            active_prop_sports.append("basketball_wnba")
        if active_prop_sports:
            from bot_propodds_nba import main as run_player_prop_bot

            tasks.append(PipelineTask(name="player_prop_bot", func=run_player_prop_bot))
        else:
            print("[seasons] Skipping player_prop_bot (NBA/MLB off-season or WNBA disabled)")
    if env_flag("ENABLE_NBA_MODEL", True):
        if is_sport_in_season("basketball_nba"):
            from model_nba import run_nba_model

            tasks.append(PipelineTask(name="model_nba", func=run_nba_model))
        else:
            print("[seasons] Skipping model_nba (NBA off-season)")
    if env_flag("ENABLE_WNBA_MODEL", True):
        if is_sport_in_season("basketball_wnba"):
            from wnba_model import run_wnba_model

            tasks.append(PipelineTask(name="model_wnba", func=run_wnba_model))
        else:
            print("[seasons] Skipping model_wnba (WNBA off-season)")
    if env_flag("ENABLE_NRFI_MODEL", True):
        if is_sport_in_season("baseball_mlb"):
            from model_nrfi import run_nrfi_model

            tasks.append(PipelineTask(name="model_nrfi", func=run_nrfi_model))
        else:
            print("[seasons] Skipping model_nrfi (MLB off-season)")
    if env_flag("ENABLE_NHL_MODEL", True):
        if is_sport_in_season("icehockey_nhl"):
            from model_nhl import run_nhl_model

            tasks.append(PipelineTask(name="model_nhl", func=run_nhl_model))
        else:
            print("[seasons] Skipping model_nhl (NHL off-season)")
    if env_flag("ENABLE_MLB_MODEL", True):
        if is_sport_in_season("baseball_mlb"):
            from model_mlb import run_mlb_model

            tasks.append(PipelineTask(name="model_mlb", func=run_mlb_model))
        else:
            print("[seasons] Skipping model_mlb (MLB off-season)")

    return tasks


def get_scan_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []
    if env_flag("ENABLE_UNIFIED_SCAN", True):
        from unified_bot import scan_markets

        tasks.append(PipelineTask(name="unified_market_scan", func=scan_markets))
    if env_flag("ENABLE_OPENING_SCAN", True):
        from continuous_scan import run_opener_scan

        tasks.append(PipelineTask(name="opener_scan", func=run_opener_scan))
    if env_flag("ENABLE_PREGAME_SCAN", True):
        from pregame_scan import run_pregame_scan

        tasks.append(PipelineTask(name="pregame_scan", func=run_pregame_scan))
    if env_flag("ENABLE_ARBITRAGE_SCAN", True):
        from arbitrage_scanner import run_arbitrage_scan

        tasks.append(PipelineTask(name="arbitrage_scan", func=run_arbitrage_scan))
    if env_flag("ENABLE_EXECUTION_DESK", True):
        from execution_scanner import run_execution_scan

        tasks.append(PipelineTask(name="execution_desk", func=run_execution_scan))
    return tasks


def get_audit_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []

    if env_flag("ENABLE_CLV_TRACKER", True):
        from clv_tracker import run_clv_tracker

        tasks.append(PipelineTask(name="clv_tracker", func=run_clv_tracker))
    if env_flag("ENABLE_SGO_GRADER", True):
        from sgo_grader import run_grader

        tasks.append(PipelineTask(name="sgo_grader", func=run_grader))
    if env_flag("ENABLE_PERFORMANCE_REPORT", True):
        from performance_report import send_performance_report

        tasks.append(PipelineTask(name="performance_report", func=send_performance_report))
    if env_flag("ENABLE_MONTE_CARLO", True):
        from risk_simulation import run_monte_carlo

        tasks.append(PipelineTask(name="monte_carlo_risk", func=run_monte_carlo))

    return tasks


def get_scraper_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []

    if env_flag("ENABLE_PYBASEBALL_FIP_SCRAPER", True):
        if is_sport_in_season("baseball_mlb"):
            try:
                from scraper_pybaseball_fip import run_fip_scraper

                tasks.append(PipelineTask(name="scraper_pybaseball_fip", func=run_fip_scraper))
            except Exception as exc:
                print(f"[scraper-loader] Skipping scraper_pybaseball_fip: {exc}")
        else:
            print("[seasons] Skipping scraper_pybaseball_fip (MLB off-season)")
    if env_flag("ENABLE_DRAFTKINGS_SCRAPER", True):
        try:
            from scraper_draftkings import scrape_dk as scrape_draftkings

            tasks.append(PipelineTask(name="scraper_draftkings", func=scrape_draftkings))
        except Exception as exc:
            print(f"[scraper-loader] Skipping scraper_draftkings: {exc}")
    if env_flag("ENABLE_BETMGM_SCRAPER", True):
        try:
            from scraper_betmgm import scrape_betmgm

            tasks.append(PipelineTask(name="scraper_betmgm", func=scrape_betmgm))
        except Exception as exc:
            print(f"[scraper-loader] Skipping scraper_betmgm: {exc}")
    if env_flag("ENABLE_FANDUEL_SCRAPER", True):
        try:
            from scraper_fanduel import scrape_fanduel

            tasks.append(PipelineTask(name="scraper_fanduel", func=scrape_fanduel))
        except Exception as exc:
            print(f"[scraper-loader] Skipping scraper_fanduel: {exc}")
    if env_flag("ENABLE_ODDSHARVESTER", True):
        try:
            from scraper_oddsharvester import scrape_oddsharvester

            tasks.append(PipelineTask(name="scraper_oddsharvester", func=scrape_oddsharvester))
        except Exception as exc:
            print(f"[scraper-loader] Skipping scraper_oddsharvester: {exc}")

    return tasks