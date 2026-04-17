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


TaskFunc = Callable[[], None]


@dataclass(frozen=True)
class PipelineTask:
    name: str
    func: TaskFunc


def get_refresh_tasks() -> List[PipelineTask]:
    return [PipelineTask(name="master_odds_fetcher", func=run_fetcher)]


def get_parallel_tasks() -> List[PipelineTask]:
    return [
        PipelineTask(name="injury_news", func=scrape_news),
        PipelineTask(name="model_nba", func=run_nba_model),
        PipelineTask(name="model_nhl", func=run_nhl_model),
        PipelineTask(name="model_mlb", func=run_mlb_model),
    ]


def get_scan_tasks() -> List[PipelineTask]:
    return [PipelineTask(name="unified_market_scan", func=scan_markets)]


def get_audit_tasks() -> List[PipelineTask]:
    return [
        PipelineTask(name="clv_tracker", func=run_clv_tracker),
        PipelineTask(name="sgo_grader", func=run_grader),
    ]


def get_scraper_tasks() -> List[PipelineTask]:
    return [
        PipelineTask(name="scraper_draftkings", func=scrape_draftkings),
        PipelineTask(name="scraper_betmgm", func=scrape_betmgm),
        PipelineTask(name="scraper_fanduel", func=scrape_fanduel),
        PipelineTask(name="scraper_prizepicks", func=scrape_prizepicks),
    ]
