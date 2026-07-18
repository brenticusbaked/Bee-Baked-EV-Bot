"""Canonical sportsbook name normalization.

The Odds API, the Supabase cache, and the user's exported betting history all
spell the same book differently (e.g. "FanDuel", "Fanduel Sportsbook",
"fanduel"). This maps any of those spellings to a single canonical key so
downstream weighting/calibration can join across sources.
"""

import re
from typing import Dict

# Canonical key -> substrings that identify the book (checked in order).
_BOOK_PATTERNS: Dict[str, tuple[str, ...]] = {
    "fanduel": ("fanduel",),
    "draftkings": ("draftkings", "dk sportsbook"),
    "dk_pick6": ("pick6", "pick 6"),
    "betmgm": ("betmgm", "mgm"),
    "caesars": ("caesars", "czr"),
    "bet365": ("bet365", "bet 365"),
    "bovada": ("bovada",),
    "novig": ("novig",),
    "kalshi": ("kalshi",),
    "polymarket": ("polymarket",),
    "prophetx": ("prophetx", "prophet x"),
    "fanatics": ("fanatics",),
    "thescore": ("thescore", "the score", "score bet", "espn bet", "espnbet"),
    "onyx": ("onyx",),
    "fliff": ("fliff",),
    "dabble": ("dabble",),
    "prizepicks": ("prizepicks", "prize picks"),
    "pinnacle": ("pinnacle",),
}


def normalize_book(name: str) -> str:
    """Return the canonical key for a book name/title, or a slugified fallback."""
    if not isinstance(name, str) or not name.strip():
        return "unknown"
    lowered = name.strip().lower()
    for canonical, patterns in _BOOK_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return canonical
    # Fallback: slugify so distinct books still bucket consistently.
    slug = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    return slug or "unknown"
