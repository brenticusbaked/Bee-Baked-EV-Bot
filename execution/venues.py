from __future__ import annotations

from typing import Dict, Iterable, List, Protocol

from execution.models import ChildOrder, Fill, OrderStatus, VenueQuote


class VenueAdapter(Protocol):
    venue_id: str

    def quote(self, symbol: str) -> VenueQuote:
        ...

    def submit(self, child_order: ChildOrder) -> Fill:
        ...


class PaperVenueAdapter:
    """Deterministic paper venue used until real venue credentials are wired."""

    def __init__(self, quote_data: VenueQuote):
        self.venue_id = quote_data.venue_id
        self.quote_data = quote_data

    def quote(self, symbol: str) -> VenueQuote:
        if symbol != self.quote_data.symbol:
            raise ValueError(f"{self.venue_id} has no quote for {symbol}")
        return self.quote_data

    def submit(self, child_order: ChildOrder) -> Fill:
        child_order.status = OrderStatus.FILLED
        fee = child_order.quantity * self.quote_data.ask_price * (self.quote_data.fee_bps / 10000.0)
        return Fill(
            child_order_id=child_order.child_order_id,
            parent_order_id=child_order.parent_order_id,
            venue_id=self.venue_id,
            symbol=child_order.symbol,
            side=child_order.side,
            quantity=child_order.quantity,
            price=self.quote_data.ask_price,
            fee=round(fee, 6),
        )


class VenueRegistry:
    def __init__(self, adapters: Iterable[VenueAdapter]):
        self.adapters: Dict[str, VenueAdapter] = {adapter.venue_id: adapter for adapter in adapters}

    def quotes(self, symbol: str) -> List[VenueQuote]:
        quotes: List[VenueQuote] = []
        for adapter in self.adapters.values():
            try:
                quotes.append(adapter.quote(symbol))
            except Exception as exc:
                print(f"Skipping quote from {adapter.venue_id}: {exc}")
        return quotes

    def submit(self, child_order: ChildOrder) -> Fill:
        adapter = self.adapters.get(child_order.venue_id)
        if not adapter:
            raise ValueError(f"Unknown venue: {child_order.venue_id}")
        return adapter.submit(child_order)

