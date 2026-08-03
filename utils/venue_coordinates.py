"""Stadium and team coordinate lookups for Open-Meteo weather integration."""

from typing import Any


def _normalize(value: Any) -> str:
    return str(value or "").lower().replace(".", "").strip()


MLB_VENUE_COORDINATES: dict[str, tuple[float, float]] = {
    # Retractable / open air parks
    "American Family Field": (43.028, -87.972),
    "Chase Field": (33.445, -112.067),
    "Truist Park": (33.891, -84.468),
    "Oriole Park at Camden Yards": (39.283, -76.622),
    "Fenway Park": (42.346, -71.097),
    "Wrigley Field": (41.948, -87.656),
    "Rate Field": (41.830, -87.634),
    "Great American Ball Park": (39.097, -84.507),
    "Progressive Field": (39.496, -81.685),
    "Coors Field": (39.755, -104.994),
    "Comerica Park": (42.339, -83.048),
    "Minute Maid Park": (29.757, -95.355),
    "Kauffman Stadium": (39.051, -94.481),
    "Angel Stadium": (33.800, -117.883),
    "Dodger Stadium": (34.073, -118.240),
    "loanDepot park": (25.778, -80.219),
    "Miller Park": (43.028, -87.972),
    "Target Field": (44.982, -93.278),
    "Citi Field": (40.758, -73.846),
    "Yankee Stadium": (40.829, -73.926),
    "Oakland Coliseum": (37.752, -122.201),
    "Citizens Bank Park": (39.906, -75.165),
    "PNC Park": (40.447, -80.006),
    "Petco Park": (32.707, -117.157),
    "Oracle Park": (37.778, -122.389),
    "T-Mobile Park": (47.591, -122.333),
    "Busch Stadium": (38.623, -90.193),
    "Tropicana Field": (27.767, -82.653),
    "Globe Life Field": (32.751, -97.083),
    "Rogers Centre": (43.641, -79.389),
    "Nationals Park": (38.873, -77.007),
    "T-Mobile Park": (47.591, -122.333),
}


NFL_TEAM_COORDINATES: dict[str, tuple[float, float]] = {
    "Arizona Cardinals": (33.527, -112.183),
    "Atlanta Falcons": (33.756, -84.401),
    "Baltimore Ravens": (39.278, -76.623),
    "Buffalo Bills": (42.774, -78.787),
    "Carolina Panthers": (35.225, -80.853),
    "Chicago Bears": (41.862, -87.617),
    "Cincinnati Bengals": (39.096, -84.516),
    "Cleveland Browns": (41.506, -81.700),
    "Dallas Cowboys": (32.748, -97.093),
    "Denver Broncos": (39.743, -105.020),
    "Detroit Lions": (42.340, -83.046),
    "Green Bay Packers": (44.501, -88.062),
    "Houston Texans": (29.685, -95.411),
    "Indianapolis Colts": (39.760, -86.164),
    "Jacksonville Jaguars": (30.324, -81.638),
    "Kansas City Chiefs": (39.049, -94.484),
    "Las Vegas Raiders": (36.090, -115.184),
    "Los Angeles Chargers": (33.953, -118.339),
    "Los Angeles Rams": (34.014, -118.288),
    "Miami Dolphins": (25.958, -80.239),
    "Minnesota Vikings": (44.974, -93.258),
    "New England Patriots": (42.091, -71.264),
    "New Orleans Saints": (29.951, -90.081),
    "New York Giants": (40.812, -74.077),
    "New York Jets": (40.812, -74.077),
    "Philadelphia Eagles": (39.901, -75.165),
    "Pittsburgh Steelers": (40.447, -80.016),
    "San Francisco 49ers": (37.713, -122.386),
    "Seattle Seahawks": (47.595, -122.332),
    "Tampa Bay Buccaneers": (27.976, -82.503),
    "Tennessee Titans": (36.166, -86.771),
    "Washington Commanders": (38.908, -76.864),
}


NFL_CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "arizona": (33.527, -112.183),
    "atlanta": (33.756, -84.401),
    "baltimore": (39.278, -76.623),
    "buffalo": (42.774, -78.787),
    "carolina": (35.225, -80.853),
    "chicago": (41.862, -87.617),
    "cincinnati": (39.096, -84.516),
    "cleveland": (41.506, -81.700),
    "dallas": (32.748, -97.093),
    "denver": (39.743, -105.020),
    "detroit": (42.340, -83.046),
    "green bay": (44.501, -88.062),
    "houston": (29.685, -95.411),
    "indianapolis": (39.760, -86.164),
    "jacksonville": (30.324, -81.638),
    "kansas city": (39.049, -94.484),
    "las vegas": (36.090, -115.184),
    "los angeles": (34.014, -118.288),
    "miami": (25.958, -80.239),
    "minnesota": (44.974, -93.258),
    "new england": (42.091, -71.264),
    "new orleans": (29.951, -90.081),
    "new york": (40.812, -74.077),
    "philadelphia": (39.901, -75.165),
    "pittsburgh": (40.447, -80.016),
    "san francisco": (37.713, -122.386),
    "seattle": (47.595, -122.332),
    "tampa bay": (27.976, -82.503),
    "tennessee": (36.166, -86.771),
    "washington": (38.908, -76.864),
}


def lookup_mlb_venue(venue_name: Any) -> tuple[float, float] | None:
    """Return (lat, lon) for an MLB venue name, or None if not found."""
    name = _normalize(venue_name)
    if not name:
        return None
    if name in MLB_VENUE_COORDINATES:
        return MLB_VENUE_COORDINATES[name]
    for key, coords in MLB_VENUE_COORDINATES.items():
        key_norm = _normalize(key)
        if name in key_norm or key_norm in name:
            return coords
    return None


def lookup_nfl_team(team_name: Any) -> tuple[float, float] | None:
    """Return (lat, lon) for an NFL team/city name, or None if not found."""
    name = _normalize(team_name)
    if not name:
        return None
    if name in NFL_TEAM_COORDINATES:
        return NFL_TEAM_COORDINATES[name]
    if name in NFL_CITY_COORDINATES:
        return NFL_CITY_COORDINATES[name]
    for key, coords in NFL_TEAM_COORDINATES.items():
        if name in _normalize(key) or _normalize(key) in name:
            return coords
    for key, coords in NFL_CITY_COORDINATES.items():
        if name in key or key in name:
            return coords
    return None
