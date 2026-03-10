"""Data model for on-chain OrderFilled events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExchangeType(str, Enum):
    CTF = "CTF"
    NEG_RISK = "NEG_RISK"


@dataclass(frozen=True)
class OrderFilledEvent:
    """Decoded OrderFilled event from CTF or NegRisk CTF Exchange."""

    tx_hash: str
    log_index: int
    block_number: int
    block_timestamp: int
    exchange_type: ExchangeType
    exchange_address: str
    order_hash: str
    maker: str
    taker: str
    maker_asset_id: int
    taker_asset_id: int
    maker_amount_filled: int
    taker_amount_filled: int
    fee: int

    @property
    def dedup_key(self) -> str:
        """Unique key for deduplication: tx_hash:log_index."""
        return f"{self.tx_hash}:{self.log_index}"

    @property
    def is_maker_buying(self) -> bool:
        """Maker is buying outcome tokens when their asset is USDC (id=0)."""
        return self.maker_asset_id == 0

    @property
    def token_id(self) -> int:
        """The conditional token ID involved in this trade."""
        if self.maker_asset_id == 0:
            # Maker pays USDC, receives tokens -> taker_asset_id is the token
            return self.taker_asset_id
        else:
            # Maker pays tokens, receives USDC -> maker_asset_id is the token
            return self.maker_asset_id

    @property
    def usdc_amount(self) -> float:
        """USDC amount involved (6 decimals)."""
        if self.maker_asset_id == 0:
            return self.maker_amount_filled / 1e6
        else:
            return self.taker_amount_filled / 1e6
