import re
from typing import Dict, Optional, Tuple

from utils.odds import parse_float


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def normalize_team_fragment(value: str) -> str:
    value = normalize_text(value)
    tokens = [token for token in value.split() if token not in {"over", "under"}]
    return " ".join(tokens)


def split_matchup(matchup: str) -> Tuple[str, str]:
    for separator in (" @ ", " vs ", " v "):
        if separator in matchup:
            away, home = matchup.split(separator, 1)
            return away.strip(), home.strip()
    return matchup.strip(), matchup.strip()


def parse_selection(market: str, selection: str) -> Dict[str, Optional[object]]:
    market_key = str(market).strip().lower()
    selection = str(selection).strip()
    selection_norm = normalize_text(selection)

    if market_key in {"h2h", "moneyline", "model_mlb_f5"}:
        return {"type": "team", "team": selection, "team_norm": normalize_team_fragment(selection)}

    if market_key in {"totals", "total"}:
        parts = selection.split()
        side = parts[0].lower() if parts else ""
        line = parse_float(parts[-1]) if parts else None
        return {"type": "total", "side": side, "line": line}

    spread_like = {
        "spreads",
        "spread",
        "model_nba_spread",
        "model_nhl_puckline",
        "puckline",
    }
    if market_key in spread_like:
        match = re.match(r"(.+?)\s+([+-]?\d+(?:\.\d+)?)$", selection)
        if match:
            team = match.group(1).strip()
            return {
                "type": "spread",
                "team": team,
                "team_norm": normalize_team_fragment(team),
                "line": float(match.group(2)),
            }
        return {"type": "spread", "team": selection, "team_norm": normalize_team_fragment(selection), "line": None}

    prop_markets = {"points", "assists", "rebounds", "goals"}
    if market_key.replace("player_", "") in prop_markets:
        prop_match = re.match(r"(.+?)\s+(over|under)\s+([0-9]+(?:\.[0-9]+)?)$", selection, flags=re.IGNORECASE)
        if prop_match:
            return {
                "type": "player_prop",
                "player": prop_match.group(1).strip(),
                "side": prop_match.group(2).lower(),
                "line": float(prop_match.group(3)),
                "stat": market_key.replace("player_", ""),
            }

    return {"type": "raw", "value": selection_norm}


def outcome_matches(selection_spec: Dict[str, Optional[object]], outcome: Dict[str, object], tolerance: float = 0.001) -> bool:
    outcome_name = str(outcome.get("name", "")).strip()
    outcome_norm = normalize_team_fragment(outcome_name)
    outcome_side = normalize_text(outcome_name)
    outcome_point = parse_float(outcome.get("point"))
    kind = selection_spec.get("type")

    if kind == "team":
        team_norm = selection_spec.get("team_norm")
        return bool(team_norm) and (team_norm == outcome_norm or team_norm in outcome_norm or outcome_norm in team_norm)

    if kind == "spread":
        team_norm = selection_spec.get("team_norm")
        line = selection_spec.get("line")
        team_match = bool(team_norm) and (team_norm == outcome_norm or team_norm in outcome_norm or outcome_norm in team_norm)
        if line is None:
            return team_match
        return team_match and outcome_point is not None and abs(outcome_point - float(line)) <= tolerance

    if kind == "total":
        line = selection_spec.get("line")
        side = normalize_text(selection_spec.get("side", ""))
        if line is None or outcome_point is None:
            return False
        return side == outcome_side and abs(outcome_point - float(line)) <= tolerance

    return False


def extract_game_scores(scores: object, away_team: str, home_team: str) -> Optional[Tuple[float, float]]:
    away_norm = normalize_team_fragment(away_team)
    home_norm = normalize_team_fragment(home_team)

    if isinstance(scores, dict):
        if "away" in scores and "home" in scores:
            away_score = parse_float(scores.get("away"))
            home_score = parse_float(scores.get("home"))
            if away_score is not None and home_score is not None:
                return away_score, home_score

        direct = {normalize_team_fragment(key): parse_float(value) for key, value in scores.items()}
        if away_norm in direct and home_norm in direct:
            return direct[away_norm], direct[home_norm]

    if isinstance(scores, list):
        mapped = {}
        for item in scores:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("team") or item.get("teamName")
            score = parse_float(item.get("score") or item.get("points") or item.get("value"))
            if name and score is not None:
                mapped[normalize_team_fragment(name)] = score
        if away_norm in mapped and home_norm in mapped:
            return mapped[away_norm], mapped[home_norm]

    return None


def grade_game_bet(market: str, selection: str, matchup: str, scores: object) -> Optional[str]:
    away_team, home_team = split_matchup(matchup)
    extracted = extract_game_scores(scores, away_team, home_team)
    if not extracted:
        return None

    away_score, home_score = extracted
    total_score = away_score + home_score
    spec = parse_selection(market, selection)
    kind = spec.get("type")

    if kind == "team":
        team_norm = spec.get("team_norm")
        winner_norm = normalize_team_fragment(away_team if away_score > home_score else home_team)
        if away_score == home_score:
            return "PUSH"
        return "WIN" if team_norm == winner_norm else "LOSS"

    if kind == "spread":
        line = spec.get("line")
        team_norm = spec.get("team_norm")
        if line is None:
            return None
        if team_norm == normalize_team_fragment(away_team):
            margin = away_score + float(line) - home_score
        elif team_norm == normalize_team_fragment(home_team):
            margin = home_score + float(line) - away_score
        else:
            return None
        if margin == 0:
            return "PUSH"
        return "WIN" if margin > 0 else "LOSS"

    if kind == "total":
        line = spec.get("line")
        side = spec.get("side")
        if line is None or side not in {"over", "under"}:
            return None
        if total_score == float(line):
            return "PUSH"
        if side == "over":
            return "WIN" if total_score > float(line) else "LOSS"
        return "WIN" if total_score < float(line) else "LOSS"

    return None
