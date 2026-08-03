import os
from datetime import datetime
from functools import lru_cache

import requests

from db_manager import load_tracker_state, save_tracker_state
from services.last_ten import build_last_ten_context_line
from utils.config import env_flag
from utils.thresholds import env_float
from utils.venue_coordinates import lookup_mlb_venue
from utils.weather import fetch_open_meteo_weather, weather_hr_boost

STATE_KEY = "mlb_hr_model_cache"
CACHE_FILE = "hr_cache.json"
HR_MODEL_EDGE_THRESHOLD = env_float("HR_MODEL_EDGE_THRESHOLD", 0.02)
HR_MODEL_MIN_UNITS = env_float("HR_MODEL_MIN_UNITS", 0.25)
HR_MODEL_BASE_DECIMAL_ODDS = env_float("HR_MODEL_BASE_DECIMAL_ODDS", 5.0)
HR_MODEL_ONLY_TIER1 = env_flag("HR_MODEL_ONLY_TIER1", True)

# Routed directly to your Positive EV / Bet Alerts Discord channel
DISCORD_WEBHOOK_URL = (
    os.environ.get("DISCORD_BET_ALERTS_WEBHOOK_URL")
    or os.environ.get("DAILY_SLIPS_WEBHOOK_URL")
    or os.environ.get("DISCORD_WEBHOOK_URL")
    or ""
).strip()


def fetch_todays_mlb_slate():
    """Fetch today's games and starting rosters using the official MLB Stats API."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today_str}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    games_list = []
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        for date_item in data.get("dates", []):
            for game in date_item.get("games", []):
                game_pk = game.get("gamePk")
                status = game.get("status", {}).get("abstractGameState")
                if str(status).strip().lower() not in {"preview", "warmup"}:
                    continue

                teams = game.get("teams", {})
                home_team = teams.get("home", {}).get("team", {}).get("name")
                away_team = teams.get("away", {}).get("team", {}).get("name")
                venue = game.get("venue", {}).get("name")
                weather = game.get("weather", {}) or {}

                games_list.append(
                    {
                        "game_pk": game_pk,
                        "status": status,
                        "home_team": home_team,
                        "away_team": away_team,
                        "venue": venue,
                        "weather": {
                            "temp": weather.get("temp"),
                            "condition": weather.get("condition"),
                            "wind": weather.get("wind"),
                            "humidity": weather.get("humidity"),
                        },
                    }
                )
        print(f"Successfully fetched {len(games_list)} games for today's MLB slate.")
    except Exception as exc:
        print(f"Failed to fetch MLB schedule: {exc}")

    return games_list


def _safe_float(val, default=0.0):
    """Safely parse float values from MLB Stats API strings like '.250'."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _team_name_variants(name: str | None) -> list[str]:
    """Return normalized variants for team-name fuzzy matching."""
    if not name:
        return []
    normalized = str(name).lower().replace(".", "").strip()
    variants = {normalized}
    words = normalized.split()
    if words:
        variants.add(words[-1])
    # Common last-name aliases for shared-city/abbreviation teams
    aliases = {
        "arizona diamondbacks": {"dbacks", "diamondbacks"},
        "chicago white sox": {"white sox"},
        "chicago cubs": {"cubs"},
        "boston red sox": {"red sox"},
        "los angeles angels": {"angels"},
        "los angeles dodgers": {"dodgers"},
        "new york mets": {"mets"},
        "new york yankees": {"yankees"},
        "san francisco giants": {"giants"},
        "st louis cardinals": {"cardinals"},
        "tampa bay rays": {"rays"},
    }
    variants.update(aliases.get(normalized, set()))
    return sorted(v for v in variants if v)


def _find_team_context(team_name: str | None, slate_context: dict) -> dict | None:
    """Look up a team's game context by any name variant."""
    if not team_name or not slate_context:
        return None
    for variant in _team_name_variants(team_name):
        if variant in slate_context:
            return slate_context[variant]
    # Final fallback: substring match against context keys
    needle = str(team_name).lower()
    for key, ctx in slate_context.items():
        if needle in key or key in needle:
            return ctx
    return None


def _season_start_for_year(year: int) -> str:
    # Conservative season start for statcast pull.
    return f"{year}-03-01"


def _park_factor_for_venue(venue_name: str | None) -> float:
    park_factors = {
        "Coors Field": 1.12,
        "Great American Ball Park": 1.09,
        "Yankee Stadium": 1.07,
        "Citizens Bank Park": 1.05,
        "Fenway Park": 1.04,
        "American Family Field": 1.03,
        "Globe Life Field": 0.98,
        "T-Mobile Park": 0.95,
        "Oracle Park": 0.94,
        "Petco Park": 0.93,
    }
    return park_factors.get(str(venue_name or "").strip(), 1.00)


def _venue_quality_adjustment(venue_name: str | None) -> float:
    park_factor = _park_factor_for_venue(venue_name)
    if park_factor >= 1.10:
        return 0.020
    if park_factor >= 1.05:
        return 0.012
    if park_factor >= 1.02:
        return 0.006
    if park_factor <= 0.94:
        return -0.010
    if park_factor <= 0.97:
        return -0.005
    return 0.0


def _parse_weather(weather: dict | None) -> dict:
    weather = weather or {}
    temp = weather.get("temp")
    wind = weather.get("wind")
    humidity = weather.get("humidity")
    condition = str(weather.get("condition") or "").strip()

    temp_f = None
    if temp not in (None, ""):
        try:
            temp_val = float(str(temp).split()[0])
            temp_f = temp_val if temp_val > 45 else (temp_val * 9.0 / 5.0) + 32.0
        except Exception:
            temp_f = None

    wind_mph = None
    if wind not in (None, ""):
        try:
            parts = str(wind).split()
            wind_mph = float(parts[0])
        except Exception:
            wind_mph = None

    humidity_pct = None
    if humidity not in (None, ""):
        try:
            humidity_pct = float(str(humidity).replace("%", ""))
        except Exception:
            humidity_pct = None

    dome = any(token in condition.lower() for token in ("dome", "indoor", "closed roof"))
    return {
        "temp_f": temp_f,
        "wind_mph": wind_mph,
        "humidity_pct": humidity_pct,
        "condition": condition,
        "dome": dome,
    }


def _weather_boost(profile: dict) -> float:
    return weather_hr_boost(profile)


def _build_team_context_map(slate_games):
    context = {}
    for game in slate_games:
        weather = _parse_weather(game.get("weather"))
        venue = game.get("venue")

        coords = lookup_mlb_venue(venue)
        if coords:
            meteo = fetch_open_meteo_weather(coords[0], coords[1])
            if meteo:
                weather["temp_f"] = meteo.get("temp_f", weather.get("temp_f"))
                weather["wind_mph"] = meteo.get("wind_mph", weather.get("wind_mph"))
                weather["wind_direction"] = meteo.get("wind_direction")
                weather["precipitation"] = meteo.get("precipitation_mm", 0.0)

        park_factor = _park_factor_for_venue(venue)
        matchup = f"{game.get('away_team')} @ {game.get('home_team')}"
        for team_name, opponent in ((game.get("home_team"), game.get("away_team")), (game.get("away_team"), game.get("home_team"))):
            if not team_name:
                continue
            ctx = {
                "matchup": matchup,
                "opponent": opponent,
                "venue": venue or "Unknown venue",
                "park_factor": park_factor,
                "weather": weather,
                "context_boost": max(
                    -0.03,
                    min(
                        0.05,
                        _venue_quality_adjustment(venue)
                        + ((park_factor - 1.0) * 0.55)
                        + _weather_boost(weather),
                    ),
                ),
            }
            context[team_name] = ctx
            for variant in _team_name_variants(team_name):
                context.setdefault(variant, ctx)
    return context


@lru_cache(maxsize=256)
def _fetch_statcast_profile(player_id: str, player_name: str, season: int):
    """Best-effort Statcast profile for exit velocity / launch angle / barrel rate."""
    try:
        from pybaseball import statcast_batter
    except Exception:
        return {}

    try:
        # pybaseball occasionally changes parameter ordering; handle the common case first.
        try:
            df = statcast_batter(_season_start_for_year(season), f"{season}-11-30", player_id)
        except TypeError:
            df = statcast_batter(f"{season}-03-01", f"{season}-11-30", player_id)

        if df is None or getattr(df, "empty", True):
            return {}

        if "launch_speed" not in df.columns or "launch_angle" not in df.columns:
            return {}

        batted = df[df["launch_speed"].notna() & df["launch_angle"].notna()].copy()
        if batted.empty:
            return {}

        launch_speed = float(batted["launch_speed"].mean())
        launch_angle = float(batted["launch_angle"].mean())
        barrel_mask = (
            (batted["launch_speed"] >= 98.0)
            & (batted["launch_angle"] >= 26.0)
            & (batted["launch_angle"] <= 30.0)
        )
        barrel_rate = float(barrel_mask.mean())

        return {
            "launch_speed": round(launch_speed, 1),
            "launch_angle": round(launch_angle, 1),
            "barrel_rate": round(barrel_rate, 3),
        }
    except Exception as exc:
        print(f"Statcast profile fetch failed for {player_name}: {exc}")
        return {}


def fetch_batter_power_stats(season=2026):
    """Pull league-wide hitting stats (HR, slugging, ISO) from the official MLB Stats API."""
    url = "https://statsapi.mlb.com/api/v1/stats"
    params = {
        "stats": "season",
        "group": "hitting",
        "season": season,
        "sportId": 1,
        "limit": 2000,
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }

    batter_cache = {}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        splits = data.get("stats", [{}])[0].get("splits", [])
        for split in splits:
            player = split.get("player", {})
            team = split.get("team", {})
            stat = split.get("stat", {})

            player_id = str(player.get("id"))
            name = player.get("fullName")
            hr = int(stat.get("homeRuns", 0))
            ab = int(stat.get("atBats", 0))
            slg = _safe_float(stat.get("slg", stat.get("sluggingPercentage", 0.0)))
            avg = _safe_float(stat.get("avg", stat.get("battingAverage", 0.0)))
            iso = slg - avg

            if ab >= 30:
                batter_cache[player_id] = {
                    "name": name,
                    "team": team.get("name") or team.get("abbreviation") or team.get("teamName"),
                    "home_runs": hr,
                    "at_bats": ab,
                    "hr_per_ab": hr / ab if ab > 0 else 0.0,
                    "slg": slg,
                    "iso": iso,
                    "statcast": _fetch_statcast_profile(player_id, str(name), season) if name else {},
                }
        print(f"Successfully compiled power metrics for {len(batter_cache)} batters.")
    except Exception as exc:
        print(f"Error fetching MLB batter stats: {exc}")

    return batter_cache


def calculate_hr_units(batter_stats, slate_context, base_unit_size=1.0, kelly_fraction=0.25):
    """
    Evaluate cached batters and compute conservative unit sizes using a strict
    Quarter-Kelly formula.
    """
    recommendations = []
    for player_id, stats in batter_stats.items():
        iso = stats.get("iso", 0.0)
        hr_per_ab = stats.get("hr_per_ab", 0.0)
        statcast = stats.get("statcast") or {}
        team_name = stats.get("team", "")
        game_context = _find_team_context(team_name, slate_context)

        if not game_context:
            continue

        is_tier_1 = iso >= 0.210 or hr_per_ab >= 0.048
        is_tier_2 = iso >= 0.180 and hr_per_ab >= 0.038

        if not (is_tier_1 or is_tier_2):
            continue
        if HR_MODEL_ONLY_TIER1 and not is_tier_1:
            continue

        # Scaled probability baseline for home run markets (~+300 odds / 4.0 decimal).
        implied_prob = min(0.30, max(0.10, hr_per_ab * 3.8))

        # Optional Statcast bump when exit velocity / launch angle are available.
        launch_speed = statcast.get("launch_speed")
        launch_angle = statcast.get("launch_angle")
        barrel_rate = statcast.get("barrel_rate", 0.0)
        statcast_boost = 0.0
        if launch_speed is not None:
            statcast_boost += max(0.0, min(0.035, (launch_speed - 88.0) * 0.0015))
        if launch_angle is not None:
            statcast_boost += max(0.0, min(0.020, (launch_angle - 10.0) * 0.0008))
        statcast_boost += max(0.0, min(0.020, barrel_rate * 0.10))
        implied_prob = min(0.34, implied_prob + statcast_boost + game_context["context_boost"])

        decimal_odds = HR_MODEL_BASE_DECIMAL_ODDS
        b = decimal_odds - 1.0
        q = 1.0 - implied_prob
        kelly_pct = (b * implied_prob - q) / b

        if kelly_pct > 0:
            tier_multiplier = 1.0 if is_tier_1 else 0.70
            if HR_MODEL_ONLY_TIER1:
                tier_multiplier = 1.0
            raw_units = kelly_pct * kelly_fraction * tier_multiplier * 10.0
            final_units = max(0.25, min(1.75, round(raw_units, 2)))
            if final_units < HR_MODEL_MIN_UNITS:
                continue

            recommendations.append(
                {
                    "name": stats["name"],
                    "team": team_name or "Unknown team",
                    "opponent": game_context.get("opponent", "Unknown opponent"),
                    "venue": game_context.get("venue", "Unknown venue"),
                    "park_factor": round(game_context.get("park_factor", 1.0), 2),
                    "iso": round(iso, 3),
                    "hr_per_ab": round(hr_per_ab, 3),
                    "tier": "Tier 1" if is_tier_1 else "Tier 2",
                    "recommended_units": final_units,
                    "statcast": statcast,
                    "weather": game_context.get("weather", {}),
                    "implied_prob": round(implied_prob, 3),
                }
            )

    recommendations.sort(key=lambda x: x["recommended_units"], reverse=True)
    return recommendations


def _build_field(rec):
    """Build a single Discord embed field for one HR recommendation."""
    last_ten = build_last_ten_context_line(
        rec["name"],
        "batter_home_runs",
        0,
        "over",
        "baseball_mlb",
    )
    statcast = rec.get("statcast") or {}
    weather = rec.get("weather") or {}
    statcast_bits = []
    if statcast.get("launch_speed") is not None:
        statcast_bits.append(f"EV `{statcast['launch_speed']}`")
    if statcast.get("launch_angle") is not None:
        statcast_bits.append(f"LA `{statcast['launch_angle']}`")
    if statcast.get("barrel_rate") is not None:
        statcast_bits.append(f"Barrel `{statcast['barrel_rate']}`")
    weather_bits = []
    if weather.get("temp_f") is not None:
        weather_bits.append(f"{weather['temp_f']:.0f}F")
    if weather.get("wind_mph") is not None:
        weather_bits.append(f"Wind `{weather['wind_mph']:.0f} mph`")
    if weather.get("condition"):
        weather_bits.append(weather["condition"])
    weather_line = " | ".join(weather_bits) if weather_bits else "Weather: unavailable"
    statcast_line = " | ".join(statcast_bits) if statcast_bits else "Statcast: unavailable"

    value = (
        f"Quarter-Kelly Sizing: **{rec['recommended_units']}u**\n"
        f"ISO: `{rec['iso']}` | HR/AB: `{rec['hr_per_ab']}`\n"
        f"Park Factor: `{rec['park_factor']}` | Implied HR Prob: `{rec['implied_prob']}`\n"
        f"{weather_line}\n"
        f"{statcast_line}\n"
        f"{last_ten}"
    )
    name = f"{rec['name']} ({rec['team']}) vs {rec['opponent']} - {rec['tier']}"
    return {
        "name": name[:256],
        "value": value[:1024],
        "inline": False,
    }


def format_discord_message(recommendations):
    """Format all home run recommendations into one or more Discord embeds."""
    if not recommendations:
        return None

    # Discord limits: 10 embeds per message and ~6000 embed characters total.
    # Keep each embed small so we stay under the character cap and field limits.
    recommendations = recommendations[:80]
    embeds = []
    chunk_size = 8
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    for i in range(0, len(recommendations), chunk_size):
        chunk = recommendations[i : i + chunk_size]
        embed: dict = {
            "color": 5763719,
            "fields": [_build_field(rec) for rec in chunk],
        }
        if i == 0:
            embed["title"] = "+EV MLB Home Run Model Recommendations"
        if i + chunk_size >= len(recommendations):
            embed["footer"] = {
                "text": f"Bee-Baked Model Engine - Quarter-Kelly - {timestamp}"
            }
        embeds.append(embed)

    return {"content": "New +EV Home Run Model slips generated!", "embeds": embeds}


def send_to_discord(payload):
    """POST the formatted payload to Discord."""
    if not payload or not DISCORD_WEBHOOK_URL:
        print("No payload generated or Discord webhook URL is missing.")
        return

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        print("Successfully sent Home Run +EV alerts to Discord channel.")
    except Exception as exc:
        print(f"Discord webhook failed for HR model: {exc}")


def run_hr_pipeline():
    print("Initializing Free-Data Home Run Model Pipeline with Quarter-Kelly Sizing...")
    slate = fetch_todays_mlb_slate()
    batter_stats = fetch_batter_power_stats()
    slate_context = _build_team_context_map(slate)

    if not batter_stats:
        print("Loading fallback HR cache state...")
        batter_stats = load_tracker_state(STATE_KEY, {})
    else:
        save_tracker_state(STATE_KEY, batter_stats, CACHE_FILE)

    recommendations = calculate_hr_units(batter_stats, slate_context)
    print(f"Generated safe unit sizing recommendations for {len(recommendations)} high-value power hitters.")

    if recommendations:
        payload = format_discord_message(recommendations)
        send_to_discord(payload)

    return {
        "detail": "hr model execution complete",
        "count": len(batter_stats),
        "recommendations": recommendations,
        "label": "updates",
    }


if __name__ == "__main__":
    run_hr_pipeline()
