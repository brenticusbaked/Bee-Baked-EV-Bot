from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from execution.models import ParentOrder


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str = ""


@dataclass
class RiskLimits:
    max_order_quantity: float = 10.0
    max_notional: float = 1000.0
    min_edge: float = 0.0
    max_symbol_exposure: float = 25.0


class RiskManager:
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.exposure_by_symbol: Dict[str, float] = {}

    def check(self, order: ParentOrder) -> RiskDecision:
        if order.quantity <= 0:
            return RiskDecision(False, "quantity must be positive")
        if order.quantity > self.limits.max_order_quantity:
            return RiskDecision(False, "order quantity exceeds max_order_quantity")

        reference_price = order.limit_price or order.fair_price
        if reference_price is None or reference_price <= 0:
            return RiskDecision(False, "order requires a positive limit_price or fair_price")
        if order.quantity * reference_price > self.limits.max_notional:
            return RiskDecision(False, "order notional exceeds max_notional")

        edge = order.metadata.get("edge")
        if edge is not None and float(edge) < self.limits.min_edge:
            return RiskDecision(False, "edge is below min_edge")

        projected = self.exposure_by_symbol.get(order.symbol, 0.0) + order.quantity
        if projected > self.limits.max_symbol_exposure:
            return RiskDecision(False, "symbol exposure exceeds max_symbol_exposure")

        return RiskDecision(True)

    def reserve(self, order: ParentOrder) -> None:
        self.exposure_by_symbol[order.symbol] = self.exposure_by_symbol.get(order.symbol, 0.0) + order.quantity

