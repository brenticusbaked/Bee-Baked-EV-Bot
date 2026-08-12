"""Shared cache-backed +EV engine for market-specific model overlays.

Reads the Supabase ``odds_cache`` blob through ``db_manager.get_master_cache``
and never touches a live odds endpoint. Pinnacle is the sole sharp baseline;
fair probabilities come from ``utils.odds.fair_probabilities_from_prices`` and
sizing from ``utils.odds.quarter_kelly_units`` so no EV math is reimplemented
here.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from db_manager import get_master_cache, is_already_logged, log_bet_to_db
from services.alerts import send_discord_alert
from services.last_ten import build_last_ten_context_line
from utils.odds import (
    decimal_to_american,
    fair_probabilities_from_prices,
    quarter_kelly_units,
)
from utils.scratch_guard import filter_valid_events

SHARP_BOOK = "pinnacle"
EMBED_COLOR = 0x2ECC71
EMBED_FOOTER = "BEE BAKED BETS | The Hive +EV Scanner"


@dataclass(frozen=True)
class MarketEdge:
    """A single priced +EV opportunity from the cached market data."""

    sport: str
    event_id: str
    matchup: str
    market_key: str
    selection: str
    player: str
    point: float | None
    book_key: str
    book_title: str
    offered_decimal: float
    fair_probability: float
    edge: float
    units: float
    # Raw outcome name ("Over", "Yes", a team). Recent-form lookups need the
    # direction, which ``selection`` has already been formatted away from.
    side: str = ""

    @property
    def offered_american(self) -> str:
        return decimal_to_american(self.offered_decimal)

    @property
    def fair_american(self) -> str:
        return decimal_to_american(1.0 / max(self.fair_probability, 0.01))


OutcomeKey = tuple[str, str, str]


def _outcome_key(outcome: dict, group_by_player: bool) -> OutcomeKey:
    """Build the grouping key consumed by ``fair_probabilities_from_prices``.

    The third element isolates each player's two-way market when de-vigging.
    Leaving it blank pools every outcome of the market into one n-way group,
    which is what a single-winner market (e.g. first basket scorer) needs.
    """
    name = str(outcome.get("name") or "").strip()
    point = outcome.get("point")
    point_text = "" if point in (None, "") else str(point)
    player = str(outcome.get("description") or "").strip() if group_by_player else ""
    return (name, point_text, player)


def _decimal_price(outcome: dict) -> float | None:
    try:
        price = float(outcome.get("price"))
    except (TypeError, ValueError):
        return None
    return price if price > 1.0 else None


def _point_value(outcome: dict) -> float | None:
    try:
        return float(outcome.get("point"))
    except (TypeError, ValueError):
        return None


def _selection_text(outcome: dict, group_by_player: bool) -> str:
    name = str(outcome.get("name") or "").strip()
    player = str(outcome.get("description") or "").strip()
    point = _point_value(outcome)
    parts: list[str] = []
    if group_by_player and player:
        parts.append(player)
    if name:
        parts.append(name)
    if point is not None:
        parts.append(f"{point:g}")
    return " ".join(parts).strip()


def _markets_for_book(book: dict, market_keys: Sequence[str]) -> Iterable[dict]:
    wanted = {str(key).lower() for key in market_keys}
    for market in book.get("markets", []) or []:
        if str(market.get("key") or "").lower() in wanted:
            yield market


def _sharp_probabilities(
    event: dict,
    market_keys: Sequence[str],
    group_by_player: bool,
) -> dict[OutcomeKey, float]:
    """De-vigged Pinnacle probabilities keyed by outcome."""
    prices: dict[OutcomeKey, float] = {}
    for book in event.get("bookmakers", []) or []:
        if str(book.get("key") or "").lower() != SHARP_BOOK:
            continue
        for market in _markets_for_book(book, market_keys):
            for outcome in market.get("outcomes", []) or []:
                price = _decimal_price(outcome)
                if price is None:
                    continue
                prices[_outcome_key(outcome, group_by_player)] = price
    if len(prices) < 2:
        return {}
    return fair_probabilities_from_prices(prices)


def find_edges(
    sport: str,
    market_keys: Sequence[str],
    ev_threshold: float,
    kelly_cap: float,
    group_by_player: bool = False,
    cache: dict | None = None,
    max_per_event: int = 5,
) -> list[MarketEdge]:
    """Return +EV soft-book prices for ``market_keys`` in ``sport``.

    ``cache`` is injectable so tests can run against a saved payload with no
    network or database access.
    """
    cache = get_master_cache() if cache is None else cache
    if not cache:
        return []

    edges: list[MarketEdge] = []
    for event in filter_valid_events(cache.get(sport, []) or [], sport):
        fair = _sharp_probabilities(event, market_keys, group_by_player)
        if not fair:
            continue

        matchup = f"{event.get('away_team', '?')} @ {event.get('home_team', '?')}"
        event_edges: list[MarketEdge] = []
        for book in event.get("bookmakers", []) or []:
            book_key = str(book.get("key") or "").lower()
            if book_key == SHARP_BOOK:
                continue
            for market in _markets_for_book(book, market_keys):
                for outcome in market.get("outcomes", []) or []:
                    offered = _decimal_price(outcome)
                    if offered is None:
                        continue
                    probability = fair.get(_outcome_key(outcome, group_by_player))
                    if not probability:
                        continue

                    edge = (offered * probability) - 1.0
                    if edge < ev_threshold:
                        continue
                    units = quarter_kelly_units(edge, offered, cap=kelly_cap)
                    if units <= 0:
                        continue

                    event_edges.append(
                        MarketEdge(
                            sport=sport,
                            event_id=str(event.get("id") or ""),
                            matchup=matchup,
                            market_key=str(market.get("key") or ""),
                            selection=_selection_text(outcome, group_by_player),
                            player=str(outcome.get("description") or "").strip(),
                            point=_point_value(outcome),
                            book_key=book_key,
                            book_title=str(book.get("title") or book_key or "Unknown"),
                            offered_decimal=offered,
                            fair_probability=probability,
                            edge=edge,
                            units=units,
                            side=str(outcome.get("name") or "").strip(),
                        )
                    )

        event_edges.sort(key=lambda item: item.edge, reverse=True)
        edges.extend(event_edges[:max_per_event])

    edges.sort(key=lambda item: item.edge, reverse=True)
    return edges


def last_ten_field(edge: MarketEdge) -> list:
    """Recent-form field, omitted entirely when there is nothing to say.

    ``build_last_ten_context_line`` returns a newline-prefixed markdown fragment
    built for plain descriptions; here it becomes its own embed field, so the
    prefix and bold label are stripped back off.
    """
    target = edge.player or edge.selection
    context = build_last_ten_context_line(
        target,
        edge.market_key,
        edge.point,
        edge.side,
        edge.sport,
        opponent=matchup_teams(edge.matchup),
    )
    body = context.replace("\n**Last 10:** ", "").strip()
    if not body or body == "unavailable.":
        return []
    return [{"name": "Last 10", "value": body[:1024], "inline": False}]


def matchup_teams(matchup: str) -> tuple:
    if " @ " not in str(matchup or ""):
        return ()
    away, home = str(matchup).split(" @ ", 1)
    return tuple(team.strip() for team in (home, away) if team.strip())


def build_embed(edge: MarketEdge) -> dict:
    return {
        "title": "🐝 +EV Alert from The Hive",
        "color": EMBED_COLOR,
        "fields": [
            {"name": "Sport / Matchup", "value": f"{edge.sport} — {edge.matchup}"[:256], "inline": False},
            {
                "name": "The +EV Play",
                "value": f"{edge.selection} @ {edge.book_title} ({edge.offered_american})"[:1024],
                "inline": False,
            },
            {
                "name": "Sharp Baseline (Pinnacle)",
                "value": (
                    f"{edge.market_key} | True probability {edge.fair_probability:.1%} "
                    f"| Fair value {edge.fair_american}"
                )[:1024],
                "inline": False,
            },
            {
                "name": "The Math",
                "value": f"EV {edge.edge:.2%} | Recommend: {edge.units:.2f}u (Quarter-Kelly)",
                "inline": False,
            },
            *last_ten_field(edge),
        ],
        "footer": {"text": EMBED_FOOTER},
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def publish_edges(
    edges: Sequence[MarketEdge],
    alert_type: str,
    model_label: str,
    source: str,
    webhook_url: str | None,
    max_alerts: int,
) -> list[dict]:
    """Log each edge to ``bets_log`` then alert; skips anything already logged.

    The bet is logged under its raw feed market key rather than a ``MODEL_*``
    label so ``clv_tracker`` can look the selection back up against Pinnacle's
    closing price without needing a bespoke alias.
    """
    published: list[dict] = []
    for edge in edges:
        if len(published) >= max_alerts:
            break
        if is_already_logged(edge.matchup, edge.market_key, edge.selection):
            continue

        was_logged = log_bet_to_db(
            edge.matchup,
            edge.market_key,
            edge.selection,
            edge.offered_american,
            edge.edge,
            f"{edge.units:.2f}",
            edge.fair_american,
            edge.sport,
            edge.event_id,
            notes=(
                f"book={edge.book_key};market={edge.market_key};model={model_label};"
                f"probability={edge.fair_probability:.4f}"
            ),
        )
        if not was_logged:
            print(f"Skipping {model_label} alert; bets_log write failed for {edge.selection}.")
            continue

        embed = build_embed(edge)
        send_discord_alert(
            payload={"embeds": [embed]},
            source=source,
            alert_type=alert_type,
            dedupe_key=f"{edge.matchup}|{edge.selection}|{edge.book_key}",
            webhook_url=webhook_url,
        )
        published.append({"matchup": edge.matchup, "selection": edge.selection, "edge": edge.edge})

    return published
