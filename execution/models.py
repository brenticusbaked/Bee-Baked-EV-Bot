from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    NEW = "NEW"
    REJECTED = "REJECTED"
    ROUTED = "ROUTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(str, Enum):
    IOC = "IOC"
    DAY = "DAY"
    GTC = "GTC"


@dataclass(frozen=True)
class VenueQuote:
    venue_id: str
    symbol: str
    ask_price: float
    bid_price: Optional[float] = None
    available_quantity: float = 0.0
    fee_bps: float = 0.0
    latency_ms: int = 100
    fill_probability: float = 0.95
    metadata: Dict[str, object] = field(default_factory=dict)

    @property
    def effective_ask(self) -> float:
        return self.ask_price * (1.0 + (self.fee_bps / 10000.0))


@dataclass
class ParentOrder:
    symbol: str
    side: Side
    quantity: float
    limit_price: Optional[float] = None
    fair_price: Optional[float] = None
    order_type: OrderType = OrderType.LIMIT
    time_in_force: TimeInForce = TimeInForce.IOC
    strategy: str = "SMART"
    source_signal: str = "manual"
    order_id: str = field(default_factory=lambda: f"PO-{uuid4().hex[:12].upper()}")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class ChildOrder:
    parent_order_id: str
    venue_id: str
    symbol: str
    side: Side
    quantity: float
    limit_price: Optional[float]
    route_score: float
    child_order_id: str = field(default_factory=lambda: f"CO-{uuid4().hex[:12].upper()}")
    status: OrderStatus = OrderStatus.NEW
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class Fill:
    child_order_id: str
    parent_order_id: str
    venue_id: str
    symbol: str
    side: Side
    quantity: float
    price: float
    fee: float = 0.0
    filled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ExecutionReport:
    parent_order: ParentOrder
    status: OrderStatus
    child_orders: List[ChildOrder]
    fills: List[Fill]
    rejected_reason: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)

