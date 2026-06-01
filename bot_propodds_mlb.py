import os

from services.sgo_props import LeagueConfig, run_prop_bot


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_MLB_BETS_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")

DEFAULT_TARGET_STATS = [
    # Pitcher props
    "strikeouts",
    "hits_allowed",
    "earned_runs",
    "walks_allowed",
    "outs",
    # Batter props
    "hits",
    "total_bases",
    "runs",
    "rbis",
    "home_runs",
    "stolen_bases",
    "singles",
    "doubles",
    "hits_runs_rbis",
]

STAT_ALIASES = {
    # Pitcher strikeouts
    "strikeouts": "strikeouts",
    "strikeout": "strikeouts",
    "ks": "strikeouts",
    "k": "strikeouts",
    "so": "strikeouts",
    "pitcher_strikeouts": "strikeouts",
    "pitching_strikeouts": "strikeouts",
    "strikeouts_pitched": "strikeouts",
    "pitcher_strikeouts_thrown": "strikeouts",
    # Hits allowed (pitcher)
    "hits_allowed": "hits_allowed",
    "pitching_hits": "hits_allowed",
    "pitcher_hits_allowed": "hits_allowed",
    # Earned runs (pitcher)
    "earned_runs": "earned_runs",
    "earned_runs_allowed": "earned_runs",
    "pitching_earnedruns": "earned_runs",
    "er": "earned_runs",
    # Walks allowed (pitcher)
    "walks_allowed": "walks_allowed",
    "pitching_walks": "walks_allowed",
    "pitcher_walks": "walks_allowed",
    # Outs (pitcher)
    "outs": "outs",
    "outs_recorded": "outs",
    "pitching_outs": "outs",
    # Batter hits
    "hits": "hits",
    "hit": "hits",
    "batting_hits": "hits",
    "h": "hits",
    # Total bases
    "total_bases": "total_bases",
    "totalbases": "total_bases",
    "tb": "total_bases",
    "batting_totalbases": "total_bases",
    # Runs scored
    "runs": "runs",
    "run": "runs",
    "runs_scored": "runs",
    "batting_runs": "runs",
    "r": "runs",
    # RBIs
    "rbis": "rbis",
    "rbi": "rbis",
    "batting_rbi": "rbis",
    "runs_batted_in": "rbis",
    # Home runs
    "home_runs": "home_runs",
    "home_run": "home_runs",
    "homeruns": "home_runs",
    "hr": "home_runs",
    "batting_homeruns": "home_runs",
    # Stolen bases
    "stolen_bases": "stolen_bases",
    "stolen_base": "stolen_bases",
    "sb": "stolen_bases",
    "batting_stolenbases": "stolen_bases",
    # Singles
    "singles": "singles",
    "single": "singles",
    "batting_singles": "singles",
    # Doubles
    "doubles": "doubles",
    "double": "doubles",
    "batting_doubles": "doubles",
    # Hits + runs + RBIs
    "hits_runs_rbis": "hits_runs_rbis",
    "hrr": "hits_runs_rbis",
    "hits_runs_and_rbis": "hits_runs_rbis",
}

STAT_LABELS = {
    "strikeouts": "STRIKEOUTS",
    "hits_allowed": "HITS ALLOWED",
    "earned_runs": "EARNED RUNS",
    "walks_allowed": "WALKS ALLOWED",
    "outs": "OUTS",
    "hits": "HITS",
    "total_bases": "TOTAL BASES",
    "runs": "RUNS",
    "rbis": "RBIS",
    "home_runs": "HOME RUNS",
    "stolen_bases": "STOLEN BASES",
    "singles": "SINGLES",
    "doubles": "DOUBLES",
    "hits_runs_rbis": "H+R+RBI",
}

MLB_PROP_CONFIG = LeagueConfig(
    league_id="MLB",
    sport_key="baseball_mlb",
    alert_title="MLB PROP ALERT",
    target_stats=frozenset(DEFAULT_TARGET_STATS),
    stat_aliases=STAT_ALIASES,
    stat_labels=STAT_LABELS,
    env_stats_var="MLB_PROP_STATS",
)


def main():
    return run_prop_bot(MLB_PROP_CONFIG, webhook_url=DISCORD_WEBHOOK_URL)


if __name__ == "__main__":
    main()
