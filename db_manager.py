pitchers = _fetch_probable_pitchers() # Keep original probable pitcher logic
recent_games = _fetch_recent_schedule() # Keep original bullpen fatigue logic
...
home_fatigue = _bullpen_fatigue_score(recent_games.get(home_team, []))