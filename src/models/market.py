"""Market information models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MarketInfo:
    """Metadata about a Polymarket market."""

    condition_id: str
    question: str
    outcome: str
    token_id: str
    neg_risk: bool = False
    active: bool = True
    end_date: str = ""


@dataclass
class OrderBookSnapshot:
    """Snapshot of an orderbook for a token."""

    token_id: str
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    ask_levels: list[tuple[float, float]] = field(default_factory=list)
    bid_levels: list[tuple[float, float]] = field(default_factory=list)

    @property
    def midpoint(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None

    def liquidity_within_pct(self, pct: float) -> float:
        """Sum price * size for all ask levels within pct% of best ask."""
        if self.best_ask is None or not self.ask_levels:
            return 0.0
        threshold = self.best_ask * (1 + pct / 100)
        total = 0.0
        for price, size in self.ask_levels:
            if price <= threshold:
                total += price * size
        return total
