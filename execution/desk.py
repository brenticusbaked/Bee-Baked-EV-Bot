from __future__ import annotations

from dataclasses import asdict
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
    def paper(cls, quotes: Iterable[VenueQuote], risk: RiskManager | None = None) -> "ExecutionDesk":
        return cls(
            venues=VenueRegistry(PaperVenueAdapter(quote) for quote in quotes),
            risk=risk or RiskManager(),
            router=SmartOrderRouter(),
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


def report_to_dict(report: ExecutionReport) -> dict:
    data = asdict(report)
    data["status"] = report.status.value
    data["parent_order"]["side"] = report.parent_order.side.value
    data["parent_order"]["order_type"] = report.parent_order.order_type.value
    data["parent_order"]["time_in_force"] = report.parent_order.time_in_force.value
    for child in data["child_orders"]:
        child["side"] = child["side"].value if hasattr(child["side"], "value") else child["side"]
        child["status"] = child["status"].value if hasattr(child["status"], "value") else child["status"]
    for fill in data["fills"]:
        fill["side"] = fill["side"].value if hasattr(fill["side"], "value") else fill["side"]
    return data

