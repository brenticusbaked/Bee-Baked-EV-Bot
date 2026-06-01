import os

from services.sgo_props import LeagueConfig, run_prop_bot


DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

DEFAULT_TARGET_STATS = [
    "points",
    "assists",
    "rebounds",
    "three_pointers",
    "steals",
    "blocks",
    "turnovers",
    "points_rebounds_assists",
    "points_rebounds",
    "points_assists",
    "rebounds_assists",
]

STAT_ALIASES = {
    "pts": "points",
    "point": "points",
    "points": "points",
    "ast": "assists",
    "assist": "assists",
    "assists": "assists",
    "reb": "rebounds",
    "rebound": "rebounds",
    "rebounds": "rebounds",
    "3pm": "three_pointers",
    "3pt": "three_pointers",
    "3ptm": "three_pointers",
    "threes": "three_pointers",
    "three_pointers": "three_pointers",
    "three_points_made": "three_pointers",
    "stl": "steals",
    "steal": "steals",
    "steals": "steals",
    "blk": "blocks",
    "block": "blocks",
    "blocks": "blocks",
    "to": "turnovers",
    "turnover": "turnovers",
    "turnovers": "turnovers",
    "pra": "points_rebounds_assists",
    "points_rebounds_assists": "points_rebounds_assists",
    "par": "points_rebounds",
    "points_rebounds": "points_rebounds",
    "pa": "points_assists",
    "points_assists": "points_assists",
    "ra": "rebounds_assists",
    "rebounds_assists": "rebounds_assists",
}

STAT_LABELS = {
    "points": "POINTS",
    "assists": "ASSISTS",
    "rebounds": "REBOUNDS",
    "three_pointers": "3PM",
    "steals": "STEALS",
    "blocks": "BLOCKS",
    "turnovers": "TURNOVERS",
    "points_rebounds_assists": "PRA",
    "points_rebounds": "PTS+REB",
    "points_assists": "PTS+AST",
    "rebounds_assists": "REB+AST",
}

NBA_PROP_CONFIG = LeagueConfig(
    league_id="NBA",
    sport_key="basketball_nba",
    alert_title="NBA PROP ALERT",
    target_stats=frozenset(DEFAULT_TARGET_STATS),
    stat_aliases=STAT_ALIASES,
    stat_labels=STAT_LABELS,
    env_stats_var="NBA_PROP_STATS",
)


def main():
    return run_prop_bot(NBA_PROP_CONFIG, webhook_url=DISCORD_WEBHOOK_URL)


if __name__ == "__main__":
    main()
