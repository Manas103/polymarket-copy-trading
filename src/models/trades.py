"""Trade execution models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass
class CopyTrade:
    """A copy trade to be submitted."""

    token_id: str
    amount_usd: float
    side: str = "BUY"
    order_type: str = "FAK"
    worst_price: float = 0.0
    neg_risk: bool = False
    signal_id: Optional[int] = None


@dataclass
class TradeResult:
    """Result of a submitted copy trade."""

    status: TradeStatus
    order_id: str = ""
    filled_amount: float = 0.0
    filled_price: float = 0.0
    error_message: str = ""
