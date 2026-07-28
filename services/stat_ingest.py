def _parse_mlb_boxscore(box: Dict[str, Any], day: str) -> List[Dict[str, Any]]:
    """Parse a statsapi boxscore_data dict into per-player log rows with stat aliasing."""
    rows: List[Dict[str, Any]] = []
    for side in ("home", "away"):
        team_block = box.get(side) or {}
        players = team_block.get("players") or {}
        team_abbr = str(((team_block.get("team") or {}).get("abbreviation")) or "")
        for player in players.values():
            name = str(((player.get("person") or {}).get("fullName")) or "").strip()
            if not name:
                continue
            stats = player.get("stats") or {}
            batting = stats.get("batting") or {}
            pitching = stats.get("pitching") or {}

            has_batting = bool(batting)
            has_pitching = bool(pitching)
            if not (has_batting or has_pitching):
                continue

            # Batting metrics
            hits = _num(batting.get("hits"))
            doubles = _num(batting.get("doubles")) or 0
            triples = _num(batting.get("triples")) or 0
            hrs = _num(batting.get("homeRuns"))
            runs = _num(batting.get("runs"))
            rbis = _num(batting.get("rbi"))
            walks = _num(batting.get("baseOnBalls"))
            sb = _num(batting.get("stolenBases"))
            k_bat = _num(batting.get("strikeOuts"))

            total_bases = None
            if hits is not None:
                total_bases = hits + doubles + 2 * triples + 3 * (hrs or 0)

            # Pitching metrics
            k_pitch = _num(pitching.get("strikeOuts"))
            outs = _ip_to_outs(pitching.get("inningsPitched"))
            hits_allowed = _num(pitching.get("hits"))
            earned_runs = _num(pitching.get("earnedRuns"))
            walks_allowed = _num(pitching.get("baseOnBalls"))

            # Determine primary strikeouts (pitching strikeouts take precedence for pitchers)
            strikeouts = k_pitch if has_pitching else k_bat

            rows.append({
                "game_date": day,
                "league": "baseball_mlb",
                "player_name": name,
                "team": team_abbr,
                "hits": hits,
                "total_bases": total_bases,
                "home_runs": hrs,
                "rbis": rbis,
                "runs": runs,
                "walks": walks,
                "stolen_bases": sb,
                "strikeouts": strikeouts,
                "outs": outs,
                "hits_allowed": hits_allowed,
                "earned_runs": earned_runs,
                "walks_allowed": walks_allowed,
                "stats": {
                    "hits": hits,
                    "total_bases": total_bases,
                    "home_runs": hrs,
                    "batter_home_runs": hrs,
                    "runs": runs,
                    "batter_runs": runs,
                    "batter_runs_scored": runs,
                    "rbis": rbis,
                    "batter_rbis": rbis,
                    "walks": walks,
                    "stolen_bases": sb,
                    "strikeouts": strikeouts,
                    "pitcher_strikeouts": k_pitch,
                    "batter_strikeouts": k_bat,
                    "outs": outs,
                    "pitcher_outs": outs,
                    "hits_allowed": hits_allowed,
                    "earned_runs": earned_runs,
                    "walks_allowed": walks_allowed,
                },
            })
    return rows