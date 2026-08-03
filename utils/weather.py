import math
import os
from typing import Any

import requests


OPEN_METEO_URL = os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")


def _km_h_to_mph(kmh: float) -> float:
    """Convert kilometers per hour to miles per hour."""
    return kmh * 0.621371


def _c_to_f(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return (celsius * 9.0 / 5.0) + 32.0


def _mm_to_inches(mm: float) -> float:
    """Convert millimeters to inches."""
    return mm / 25.4


def fetch_open_meteo_weather(latitude: float, longitude: float, forecast_hour: int | None = None) -> dict[str, Any] | None:
    """Fetch Open-Meteo weather for a given location.

    If ``forecast_hour`` is provided (0-23), the closest hourly forecast value
    is returned; otherwise current conditions are used.
    """
    params: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation",
        "hourly": "temperature_2m,wind_speed_10m,wind_direction_10m,precipitation",
        "timezone": "auto",
        "forecast_days": 1,
    }

    try:
        response = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        print(f"Open-Meteo API request failed: {exc}")
        return None

    result: dict[str, Any] = {}
    if forecast_hour is not None:
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return None
        idx = max(0, min(forecast_hour, len(times) - 1))
        result["temperature_2m_c"] = float(hourly.get("temperature_2m", [0.0])[idx])
        result["wind_speed_10m_kmh"] = float(hourly.get("wind_speed_10m", [0.0])[idx])
        result["wind_direction_10m"] = float(hourly.get("wind_direction_10m", [0.0])[idx])
        result["precipitation_mm"] = float(hourly.get("precipitation", [0.0])[idx])
    else:
        current = data.get("current", {})
        result["temperature_2m_c"] = float(current.get("temperature_2m", 0.0))
        result["wind_speed_10m_kmh"] = float(current.get("wind_speed_10m", 0.0))
        result["wind_direction_10m"] = float(current.get("wind_direction_10m", 0.0))
        result["precipitation_mm"] = float(current.get("precipitation", 0.0))

    result["temp_f"] = _c_to_f(result["temperature_2m_c"])
    result["wind_mph"] = _km_h_to_mph(result["wind_speed_10m_kmh"])
    result["precipitation_in"] = _mm_to_inches(result["precipitation_mm"])
    result["latitude"] = data.get("latitude")
    result["longitude"] = data.get("longitude")
    return result


def weather_hr_boost(weather: dict[str, Any] | None) -> float:
    """Return a probability boost for MLB home-run probability from weather.

    Positive for warm/calm/dry conditions; negative for cold/wet/windy.
    """
    if not weather or weather.get("dome"):
        return 0.0

    boost = 0.0
    temp_f = weather.get("temp_f")
    wind_mph = weather.get("wind_mph")
    precipitation_mm = weather.get("precipitation", weather.get("precipitation_mm", 0.0))

    if temp_f is not None:
        # Warm air increases distance; very cold saps it.
        boost += max(-0.01, min(0.02, (temp_f - 72.0) * 0.001))

    if wind_mph is not None:
        # Wind speed in mph helps slightly up to ~15 mph, then becomes a drag.
        boost += max(-0.01, min(0.015, (wind_mph - 5.0) * 0.0015))

    humidity_pct = weather.get("humidity_pct")
    if humidity_pct is not None:
        # Higher humidity reduces ball carry slightly.
        boost += max(-0.005, min(0.008, (humidity_pct - 50.0) * 0.0002))

    if precipitation_mm is not None and precipitation_mm > 0.0:
        # Any precipitation makes the ball slicker and reduces carry.
        boost -= min(0.015, 0.005 + precipitation_mm * 0.002)

    condition = str(weather.get("condition") or "").lower()
    if any(token in condition for token in ("rain", "drizzle", "mist", "fog")):
        boost -= 0.004

    return boost


def nfl_weather_total_shift(weather: dict[str, Any] | None) -> float:
    """Return an expected total-point shift for an NFL game based on weather.

    Wet and/or windy conditions lower expected scoring.  Cold and hot extremes
    have a small asymmetric effect on scoring.
    """
    if not weather:
        return 0.0

    shift = 0.0
    wind_mph = weather.get("wind_mph", 0.0) or 0.0
    precipitation_mm = weather.get("precipitation", weather.get("precipitation_mm", 0.0)) or 0.0
    temp_f = weather.get("temp_f")

    # Wind hurts passing accuracy and deep balls.
    if wind_mph > 10.0:
        shift -= 0.25 * (wind_mph - 10.0)
    if wind_mph > 20.0:
        shift -= 0.5 * (wind_mph - 20.0)

    # Precipitation makes the ball harder to grip and field conditions worse.
    if precipitation_mm > 2.0:
        shift -= 2.0
    elif precipitation_mm > 0.2:
        shift -= 1.0

    # Temperature extremes.
    if temp_f is not None:
        if temp_f < 32.0:
            shift -= 0.5
        elif temp_f > 80.0:
            shift += 0.5

    return shift
