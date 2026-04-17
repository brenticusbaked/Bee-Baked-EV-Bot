from dataclasses import dataclass
from typing import Callable, List

from clv_tracker import run_clv_tracker
from master_odds_fetcher import run_fetcher
from model_mlb import run_mlb_model
from model_nba import run_nba_model
from model_nhl import run_nhl_model
from scraper_betmgm import scrape_betmgm
from scraper_bot import scrape_news
from scraper_draftkings import scrape_draftkings
from scraper_fanduel import scrape_fanduel
from scraper_prizepicks import scrape_prizepicks
from sgo_grader import run_grader
from unified_bot import scan_markets
from utils.config import env_flag


TaskFunc = Callable[[], None]


@dataclass(frozen=True)
class PipelineTask:
    name: str
    func: TaskFunc


def get_refresh_tasks() -> List[PipelineTask]:
    return [PipelineTask(name="master_odds_fetcher", func=run_fetcher)]


def get_parallel_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []
    if env_flag("ENABLE_NEWS", True):
        tasks.append(PipelineTask(name="injury_news", func=scrape_news))
    if env_flag("ENABLE_NBA_MODEL", True):
        tasks.append(PipelineTask(name="model_nba", func=run_nba_model))
    if env_flag("ENABLE_NHL_MODEL", True):
        tasks.append(PipelineTask(name="model_nhl", func=run_nhl_model))
    if env_flag("ENABLE_MLB_MODEL", True):
        tasks.append(PipelineTask(name="model_mlb", func=run_mlb_model))
    return tasks


def get_scan_tasks() -> List[PipelineTask]:
    if env_flag("ENABLE_UNIFIED_SCAN", True):
        return [PipelineTask(name="unified_market_scan", func=scan_markets)]
    return []


def get_audit_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []
    if env_flag("ENABLE_CLV_TRACKER", True):
        tasks.append(PipelineTask(name="clv_tracker", func=run_clv_tracker))
    if env_flag("ENABLE_SGO_GRADER", True):
        tasks.append(PipelineTask(name="sgo_grader", func=run_grader))
    return tasks


def get_scraper_tasks() -> List[PipelineTask]:
    tasks: List[PipelineTask] = []
    if env_flag("ENABLE_DRAFTKINGS_SCRAPER", True):
        tasks.append(PipelineTask(name="scraper_draftkings", func=scrape_draftkings))
    if env_flag("ENABLE_BETMGM_SCRAPER", True):
        tasks.append(PipelineTask(name="scraper_betmgm", func=scrape_betmgm))
    if env_flag("ENABLE_FANDUEL_SCRAPER", True):
        tasks.append(PipelineTask(name="scraper_fanduel", func=scrape_fanduel))
    if env_flag("ENABLE_PRIZEPICKS_SCRAPER", True):
        tasks.append(PipelineTask(name="scraper_prizepicks", func=scrape_prizepicks))
    return tasks
