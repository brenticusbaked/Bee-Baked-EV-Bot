from clv_tracker import run_clv_tracker
from master_odds_fetcher import run_fetcher
from sgo_grader import run_grader
from services.pipeline import run_master_pipeline
from unified_bot import scan_markets


def refresh_cloud_cache():
    run_fetcher()


def scan_for_ev_bets():
    scan_markets()


def audit_clv():
    run_clv_tracker()


def grade_bets():
    run_grader()


if __name__ == "__main__":
    run_master_pipeline()
