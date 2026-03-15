"""Trade signal models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.models.events import OrderFilledEvent


class SignalAction(str, Enum):
    COPY_BUY = "COPY_BUY"
    COPY_SELL = "COPY_SELL"
    SKIP_SELL = "SKIP_SELL"
    SKIP_BELOW_THRESHOLD = "SKIP_BELOW_THRESHOLD"
    SKIP_NOT_WHALE = "SKIP_NOT_WHALE"
    SKIP_EXCHANGE_TAKER = "SKIP_EXCHANGE_TAKER"
    SKIP_DUPLICATE = "SKIP_DUPLICATE"
    SKIP_LOW_CONVICTION = "SKIP_LOW_CONVICTION"
    SKIP_MARKET_RESOLUTION_FAILED = "SKIP_MARKET_RESOLUTION_FAILED"
    SKIP_MARKET_EXPIRING = "SKIP_MARKET_EXPIRING"
    SKIP_LOW_LIQUIDITY = "SKIP_LOW_LIQUIDITY"
    SKIP_PRICE_MOVED = "SKIP_PRICE_MOVED"
    SKIP_WHALE_EXITING = "SKIP_WHALE_EXITING"
    SKIP_ACCUMULATOR_FIRED = "SKIP_ACCUMULATOR_FIRED"
    SKIP_SLIPPAGE_HIGH = "SKIP_SLIPPAGE_HIGH"
    SKIP_NO_ORDERBOOK = "SKIP_NO_ORDERBOOK"
    SKIP_PRICE_TOO_HIGH = "SKIP_PRICE_TOO_HIGH"


@dataclass
class TradeSignal:
    """A signal generated from a detected whale trade."""

    event: OrderFilledEvent
    action: SignalAction
    whale_address: str = ""

    # Market context (populated by MarketResolver)
    market_question: str = ""
    outcome: str = ""
    condition_id: str = ""
    neg_risk: bool = False

    # Conviction context
    conviction_pct: float = 0.0

    # Pricing context
    whale_price: float = 0.0
    current_best_ask: Optional[float] = None
    current_midpoint: Optional[float] = None

    @property
    def should_copy(self) -> bool:
        return self.action in (SignalAction.COPY_BUY, SignalAction.COPY_SELL)

    @property
    def is_sell(self) -> bool:
        return self.action == SignalAction.COPY_SELL
