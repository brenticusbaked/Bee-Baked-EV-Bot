from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Iterable, List

from execution.models import ExecutionReport, OrderStatus, ParentOrder, VenueQuote
from execution.risk import RiskManager
from execution.router import SmartOrderRouter
from execution.tca import execution_metrics
from execution.venues import PaperVenueAdapter, VenueRegistry


class ExecutionDesk:
    def __init__(self, venues: VenueRegistry, risk: RiskManager, router: SmartOrderRouter):
        self.venues = venues
        self.risk = risk
        self.router = router

    @classmethod
    def paper(
        cls,
        quotes: Iterable[VenueQuote],
        risk: RiskManager | None = None,
        router: SmartOrderRouter | None = None,
    ) -> "ExecutionDesk":
        return cls(
            venues=VenueRegistry(PaperVenueAdapter(quote) for quote in quotes),
            risk=risk or RiskManager(),
            router=router or SmartOrderRouter(),
        )

    def execute(self, order: ParentOrder) -> ExecutionReport:
        decision = self.risk.check(order)
        if not decision.accepted:
            return ExecutionReport(order, OrderStatus.REJECTED, [], [], decision.reason)

        quotes = self.venues.quotes(order.symbol)
        child_orders = self.router.route(order, quotes)
        if not child_orders:
            return ExecutionReport(order, OrderStatus.REJECTED, [], [], "no executable venue quotes")

        self.risk.reserve(order)
        fills = []
        for child in child_orders:
            child.status = OrderStatus.ROUTED
            fills.append(self.venues.submit(child))

        filled_qty = sum(fill.quantity for fill in fills)
        status = OrderStatus.FILLED if filled_qty >= order.quantity else OrderStatus.PARTIALLY_FILLED
        return ExecutionReport(order, status, child_orders, fills, metrics=execution_metrics(order, fills))


def _json_safe(value):
    """Recursively convert ``asdict`` output into JSON-encodable values.

    ``ParentOrder.created_at`` and ``Fill.filled_at`` survive ``asdict`` as real
    ``datetime`` objects, which the Supabase client cannot encode; the local
    ledger hid this because it dumps with ``default=str``.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def report_to_dict(report: ExecutionReport) -> dict:
    data = _json_safe(asdict(report))
    data["status"] = report.status.value
    return data
