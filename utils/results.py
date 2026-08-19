"""Canonical bet result and sportsbook parsing.

Two vocabularies ended up in ``bets_log``: the graders write ``WIN``/``LOSS``/
``PUSH`` while the historical CSV import wrote ``win``/``loss``/``push``, so every
reader that compared against the upper-case strings counted 22k imported bets as
ungraded and reported an empty record. Normalise on read instead of trusting the
writer.
"""

from __future__ import annotations

import re

from utils.book_names import normalize_book

WIN = "WIN"
LOSS = "LOSS"
PUSH = "PUSH"
GRADED_RESULTS = (WIN, LOSS, PUSH)

_RESULT_SYNONYMS = {
    WIN: {"win", "won", "w", "settled_win", "cash", "cashed"},
    LOSS: {"loss", "lost", "lose", "l", "settled_loss"},
    # A void/refund returns the stake, which is a push as far as ROI is concerned.
    PUSH: {"push", "void", "tie", "draw", "refund", "settled_push", "settled_void", "cancelled", "canceled"},
}
_RESULT_LOOKUP = {alias: canonical for canonical, aliases in _RESULT_SYNONYMS.items() for alias in aliases}

# The importer stored display names ("Fanduel Sportsbook"), the scanners store
# Odds API keys ("fanduel"); `normalize_book` collapses both onto one key.
_BOOK_NOTE_RE = re.compile(r"\bbook(?:_key)?=([^;]+)", re.IGNORECASE)


def normalize_result(value: object) -> str:
    """Return ``WIN``/``LOSS``/``PUSH``, or ``""`` when the bet is not graded."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return _RESULT_LOOKUP.get(text, "")


def is_graded(value: object) -> bool:
    return normalize_result(value) in GRADED_RESULTS


def book_from_notes(notes: object) -> str:
    """Pull the book out of a ``bets_log.notes`` string.

    Accepts both ``book=`` (display name in some writers) and ``book_key=``.
    """
    text = str(notes or "")
    if not text:
        return "unknown"
    match = _BOOK_NOTE_RE.search(text)
    if not match:
        return "unknown"
    return normalize_book(match.group(1).strip())
