from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Optional


@dataclass(frozen=True)
class VenueScore:
    venue_id: str
    score: float
    sample_size: int
    fill_rate: float
    average_edge_capture: float
    average_latency_ms: Optional[float]
    average_fee: float


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_venue_scores(rows: Iterable[dict], min_sample: int = 3) -> Dict[str, VenueScore]:
    grouped = defaultdict(
        lambda: {
            "sample_size": 0,
            "routed_quantity": 0.0,
            "filled_quantity": 0.0,
            "edge_capture_sum": 0.0,
            "edge_capture_count": 0,
            "latency_sum": 0.0,
            "latency_count": 0,
            "fee_sum": 0.0,
        }
    )
    for row in rows:
        venue_id = str(row.get("venue_id") or "").strip()
        if not venue_id:
            continue
        bucket = grouped[venue_id]
        bucket["sample_size"] += 1
        bucket["routed_quantity"] += float(row.get("routed_quantity") or 0.0)
        bucket["filled_quantity"] += float(row.get("filled_quantity") or 0.0)
        bucket["fee_sum"] += float(row.get("fee") or 0.0)

        edge_capture = row.get("edge_capture")
        if edge_capture is not None:
            bucket["edge_capture_sum"] += float(edge_capture)
            bucket["edge_capture_count"] += 1

        latency_ms = row.get("latency_ms")
        if latency_ms is not None:
            bucket["latency_sum"] += float(latency_ms)
            bucket["latency_count"] += 1

    scores: Dict[str, VenueScore] = {}
    for venue_id, bucket in grouped.items():
        sample_size = bucket["sample_size"]
        routed_quantity = bucket["routed_quantity"]
        filled_quantity = bucket["filled_quantity"]
        fill_rate = filled_quantity / routed_quantity if routed_quantity else 0.0
        average_edge_capture = (
            bucket["edge_capture_sum"] / bucket["edge_capture_count"]
            if bucket["edge_capture_count"]
            else 0.0
        )
        average_latency_ms = (
            bucket["latency_sum"] / bucket["latency_count"]
            if bucket["latency_count"]
            else None
        )
        average_fee = bucket["fee_sum"] / sample_size if sample_size else 0.0

        confidence = min(1.0, sample_size / max(1, min_sample))
        fill_component = clamp(fill_rate, 0.0, 1.0)
        edge_component = clamp(0.5 + (average_edge_capture * 10.0), 0.0, 1.0)
        latency_component = 1.0 if average_latency_ms is None else clamp(1.0 - (average_latency_ms / 1000.0), 0.0, 1.0)
        fee_component = clamp(1.0 - average_fee, 0.0, 1.0)
        raw_score = (
            0.45 * fill_component
            + 0.35 * edge_component
            + 0.15 * latency_component
            + 0.05 * fee_component
        )
        score = (raw_score * confidence) + (1.0 * (1.0 - confidence))
        scores[venue_id] = VenueScore(
            venue_id=venue_id,
            score=round(clamp(score, 0.25, 1.25), 6),
            sample_size=sample_size,
            fill_rate=round(fill_rate, 6),
            average_edge_capture=round(average_edge_capture, 6),
            average_latency_ms=round(average_latency_ms, 6) if average_latency_ms is not None else None,
            average_fee=round(average_fee, 6),
        )
    return scores

