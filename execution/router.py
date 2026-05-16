from __future__ import annotations

from typing import Dict, Iterable, List

from execution.models import ChildOrder, ParentOrder, Side, VenueQuote


class SmartOrderRouter:
    """Splits parent orders across venues using price, liquidity, latency, and fill odds."""

    def __init__(self, max_child_orders: int = 4, venue_scores: Dict[str, float] | None = None):
        self.max_child_orders = max(1, int(max_child_orders))
        self.venue_scores = venue_scores or {}

    def route(self, order: ParentOrder, quotes: Iterable[VenueQuote]) -> List[ChildOrder]:
        candidates = [
            quote for quote in quotes
            if quote.symbol == order.symbol and quote.available_quantity > 0
        ]
        if order.side == Side.BUY and order.limit_price is not None:
            candidates = [quote for quote in candidates if quote.effective_ask <= order.limit_price]
        if not candidates:
            return []

        higher_is_better = order.metadata.get("price_mode") == "higher_is_better"
        ranked = sorted(candidates, key=lambda quote: self._quote_cost(quote, higher_is_better))
        if order.strategy.upper() == "TWAP":
            return self._twap(order, ranked)
        return self._liquidity_weighted(order, ranked)

    def _score_quote(self, quote: VenueQuote) -> float:
        return self._quote_cost(quote, higher_is_better=False)

    def _quote_cost(self, quote: VenueQuote, higher_is_better: bool) -> float:
        latency_penalty = quote.latency_ms / 100000.0
        fill_penalty = max(0.0, 1.0 - quote.fill_probability) / 1000.0
        venue_penalty = max(0.0, 1.0 - float(self.venue_scores.get(quote.venue_id, 1.0))) / 100.0
        price_component = -quote.effective_ask if higher_is_better else quote.effective_ask
        return price_component + latency_penalty + fill_penalty + venue_penalty

    def _liquidity_weighted(self, order: ParentOrder, ranked: List[VenueQuote]) -> List[ChildOrder]:
        remaining = order.quantity
        children: List[ChildOrder] = []
        for quote in ranked[: self.max_child_orders]:
            if remaining <= 0:
                break
            quantity = min(remaining, quote.available_quantity)
            remaining -= quantity
            children.append(self._child(order, quote, quantity))
        return children

    def _twap(self, order: ParentOrder, ranked: List[VenueQuote]) -> List[ChildOrder]:
        selected = ranked[: self.max_child_orders]
        if not selected:
            return []
        slice_qty = order.quantity / len(selected)
        remaining = order.quantity
        children: List[ChildOrder] = []
        for quote in selected:
            if remaining <= 0:
                break
            quantity = min(slice_qty, quote.available_quantity, remaining)
            remaining -= quantity
            children.append(self._child(order, quote, quantity))
        return children

    def _child(self, order: ParentOrder, quote: VenueQuote, quantity: float) -> ChildOrder:
        return ChildOrder(
            parent_order_id=order.order_id,
            venue_id=quote.venue_id,
            symbol=order.symbol,
            side=order.side,
            quantity=round(quantity, 6),
            limit_price=order.limit_price,
            route_score=self._quote_cost(quote, order.metadata.get("price_mode") == "higher_is_better"),
            metadata={
                "ask_price": quote.ask_price,
                "effective_ask": quote.effective_ask,
                "fee_bps": quote.fee_bps,
                "latency_ms": quote.latency_ms,
                "fill_probability": quote.fill_probability,
                "venue_score": self.venue_scores.get(quote.venue_id, 1.0),
            },
        )
