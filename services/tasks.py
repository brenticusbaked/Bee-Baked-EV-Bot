from dataclasses import dataclass
from typing import Callable, List

from utils.config import env_flag


TaskFunc = Callable[[], None]


@dataclass(frozen=True)
class PipelineTask:
    name: str
    func: TaskFunc


def get_refresh_tasks() -> List[PipelineTask]:
    from master_odds_fetcher import run_fetcher

    return [PipelineTask(name="master_odds_fetcher", func=run_fetcher)]


def get_parallel_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []

    if env_flag("ENABLE_NEWS", True):
        from scraper_bot import scrape_news

        tasks.append(PipelineTask(name="injury_news", func=scrape_news))
    if env_flag("ENABLE_NBA_PROP_BOT", True):
        from bot_propodds_nba import main as run_nba_prop_bot

        tasks.append(PipelineTask(name="nba_prop_bot", func=run_nba_prop_bot))
    if env_flag("ENABLE_NBA_MODEL", True):
        from model_nba import run_nba_model

        tasks.append(PipelineTask(name="model_nba", func=run_nba_model))
    if env_flag("ENABLE_NHL_MODEL", True):
        from model_nhl import run_nhl_model

        tasks.append(PipelineTask(name="model_nhl", func=run_nhl_model))
    if env_flag("ENABLE_MLB_MODEL", True):
        from model_mlb import run_mlb_model

        tasks.append(PipelineTask(name="model_mlb", func=run_mlb_model))

    return tasks


def get_scan_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []
    if env_flag("ENABLE_UNIFIED_SCAN", True):
        from unified_bot import scan_markets

        tasks.append(PipelineTask(name="unified_market_scan", func=scan_markets))
    if env_flag("ENABLE_EXECUTION_DESK", False):
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
    if env_flag("ENABLE_PERFORMANCE_REPORT", False):
        from performance_report import send_performance_report

        tasks.append(PipelineTask(name="performance_report", func=send_performance_report))
    if env_flag("ENABLE_MONTE_CARLO", False):
        from risk_simulation import run_monte_carlo

        tasks.append(PipelineTask(name="monte_carlo_risk", func=run_monte_carlo))

    return tasks


def get_scraper_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []

    if env_flag("ENABLE_PYBASEBALL_FIP_SCRAPER", True):
        from scraper_pybaseball_fip import run_fip_scraper

        tasks.append(PipelineTask(name="scraper_pybaseball_fip", func=run_fip_scraper))
    if env_flag("ENABLE_DRAFTKINGS_SCRAPER", True):
        from scraper_draftkings import scrape_dk as scrape_draftkings

        tasks.append(PipelineTask(name="scraper_draftkings", func=scrape_draftkings))
    if env_flag("ENABLE_BETMGM_SCRAPER", False):
        from scraper_betmgm import scrape_betmgm

        tasks.append(PipelineTask(name="scraper_betmgm", func=scrape_betmgm))
    if env_flag("ENABLE_FANDUEL_SCRAPER", True):
        from scraper_fanduel import scrape_fanduel

        tasks.append(PipelineTask(name="scraper_fanduel", func=scrape_fanduel))
    if env_flag("ENABLE_PRIZEPICKS_SCRAPER", True):
        from scraper_prizepicks import scrape_prizepicks

        tasks.append(PipelineTask(name="scraper_prizepicks", func=scrape_prizepicks))
    if env_flag("ENABLE_ODDSHARVESTER", False):
        from scraper_oddsharvester import scrape_oddsharvester

        tasks.append(PipelineTask(name="scraper_oddsharvester", func=scrape_oddsharvester))

    return tasks
