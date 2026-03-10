"""ABI decoding and deduplication for OrderFilled events."""

from __future__ import annotations

import logging
from typing import Sequence

from eth_abi import decode
from web3.types import LogReceipt

from config import CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE
from src.models.events import ExchangeType, OrderFilledEvent

logger = logging.getLogger(__name__)

# OrderFilled(bytes32 indexed orderHash, address indexed maker,
#             address indexed taker, uint256 makerAssetId, uint256 takerAssetId,
#             uint256 makerAmountFilled, uint256 takerAmountFilled, uint256 fee)
# First 3 params are indexed (topics[1..3]); remaining 5 are in `data`.
ORDER_FILLED_DATA_TYPES = [
    "uint256",  # makerAssetId
    "uint256",  # takerAssetId
    "uint256",  # makerAmountFilled
    "uint256",  # takerAmountFilled
    "uint256",  # fee
]

_EXCHANGE_MAP = {
    CTF_EXCHANGE.lower(): ExchangeType.CTF,
    NEG_RISK_CTF_EXCHANGE.lower(): ExchangeType.NEG_RISK,
}

_EXCHANGE_ADDRESSES = {CTF_EXCHANGE.lower(), NEG_RISK_CTF_EXCHANGE.lower()}


class EventParser:
    """Decode raw logs into OrderFilledEvent objects with deduplication."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def parse_logs(
        self, logs: Sequence[LogReceipt], block_timestamps: dict[int, int]
    ) -> list[OrderFilledEvent]:
        """Parse raw log receipts into deduplicated OrderFilledEvent list."""
        events: list[OrderFilledEvent] = []
        for log in logs:
            try:
                event = self._decode_log(log, block_timestamps)
            except Exception:
                logger.exception("Failed to decode log: %s", log)
                continue

            if event is None:
                continue

            # Dedup within this batch
            if event.dedup_key in self._seen:
                continue
            self._seen.add(event.dedup_key)

            # Skip taker-focused summary events (taker == exchange address)
            if event.taker.lower() in _EXCHANGE_ADDRESSES:
                logger.debug(
                    "Skipping exchange-taker summary event: %s", event.dedup_key
                )
                continue

            events.append(event)

        return events

    def _decode_log(
        self, log: LogReceipt, block_timestamps: dict[int, int]
    ) -> OrderFilledEvent | None:
        address = log["address"].lower()
        exchange_type = _EXCHANGE_MAP.get(address)
        if exchange_type is None:
            return None

        # Indexed params from topics
        topics = log["topics"]
        order_hash_raw = topics[1]
        if isinstance(order_hash_raw, bytes):
            order_hash = "0x" + order_hash_raw.hex()
        else:
            order_hash = order_hash_raw if order_hash_raw.startswith("0x") else "0x" + order_hash_raw

        maker_raw = topics[2]
        if isinstance(maker_raw, bytes):
            maker = "0x" + maker_raw[-20:].hex()
        else:
            maker = "0x" + maker_raw[-40:]

        taker_raw = topics[3]
        if isinstance(taker_raw, bytes):
            taker = "0x" + taker_raw[-20:].hex()
        else:
            taker = "0x" + taker_raw[-40:]

        # Non-indexed params from data
        data = bytes.fromhex(log["data"].hex() if isinstance(log["data"], bytes) else log["data"][2:])
        decoded = decode(ORDER_FILLED_DATA_TYPES, data)

        maker_asset_id = decoded[0]
        taker_asset_id = decoded[1]
        maker_amount_filled = decoded[2]
        taker_amount_filled = decoded[3]
        fee = decoded[4]

        tx_hash = log["transactionHash"]
        if isinstance(tx_hash, bytes):
            tx_hash = "0x" + tx_hash.hex()

        block_number = log["blockNumber"]
        timestamp = block_timestamps.get(block_number, 0)

        return OrderFilledEvent(
            tx_hash=tx_hash,
            log_index=log["logIndex"],
            block_number=block_number,
            block_timestamp=timestamp,
            exchange_type=exchange_type,
            exchange_address=log["address"],
            order_hash=order_hash,
            maker=maker,
            taker=taker,
            maker_asset_id=maker_asset_id,
            taker_asset_id=taker_asset_id,
            maker_amount_filled=maker_amount_filled,
            taker_amount_filled=taker_amount_filled,
            fee=fee,
        )

    def mark_seen(self, dedup_key: str) -> None:
        """Mark an event as already seen (loaded from DB on startup)."""
        self._seen.add(dedup_key)
