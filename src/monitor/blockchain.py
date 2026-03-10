"""Blockchain monitor: async polling of Polygon for OrderFilled events."""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from web3 import AsyncWeb3
from web3.types import LogReceipt

from config import AppConfig

logger = logging.getLogger(__name__)


class BlockchainMonitor:
    """Polls Polygon via eth_getLogs for OrderFilled events from both exchanges."""

    def __init__(self, config: AppConfig, w3: AsyncWeb3) -> None:
        self._config = config
        self._w3 = w3
        self._polygon = config.polygon
        self._last_block: int | None = None

    async def get_latest_safe_block(self) -> int:
        """Get the latest block minus reorg safety buffer."""
        latest = await self._w3.eth.block_number
        return max(0, latest - self._polygon.reorg_safety_blocks)

    def set_last_block(self, block_number: int) -> None:
        self._last_block = block_number

    async def poll_events(self) -> AsyncIterator[tuple[list[LogReceipt], dict[int, int]]]:
        """Continuously poll for new OrderFilled events.

        Yields (logs, block_timestamps) tuples.
        """
        if self._last_block is None:
            self._last_block = await self.get_latest_safe_block()
            logger.info("Starting from block %d", self._last_block)

        while True:
            try:
                safe_block = await self.get_latest_safe_block()

                if safe_block <= self._last_block:
                    await asyncio.sleep(self._polygon.poll_interval_seconds)
                    continue

                from_block = self._last_block + 1
                to_block = min(
                    safe_block,
                    from_block + self._polygon.max_blocks_per_query - 1,
                )

                logger.info("Processing blocks %d-%d", from_block, to_block)

                logs = await self._fetch_logs(from_block, to_block)
                block_timestamps = await self._get_block_timestamps(logs)

                self._last_block = to_block
                yield logs, block_timestamps

                # If we're caught up, sleep; otherwise process next batch immediately
                if to_block >= safe_block:
                    await asyncio.sleep(self._polygon.poll_interval_seconds)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error polling blocks")
                await asyncio.sleep(self._polygon.poll_interval_seconds * 2)

    async def _fetch_logs(
        self, from_block: int, to_block: int
    ) -> list[LogReceipt]:
        """Fetch OrderFilled logs from both exchanges in a single call."""
        logs = await self._w3.eth.get_logs(
            {
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": [
                    self._polygon.ctf_exchange,
                    self._polygon.neg_risk_ctf_exchange,
                ],
                "topics": [self._polygon.order_filled_event_sig],
            }
        )
        return list(logs)

    async def _get_block_timestamps(
        self, logs: list[LogReceipt]
    ) -> dict[int, int]:
        """Fetch timestamps for all unique blocks in the logs."""
        block_numbers = {log["blockNumber"] for log in logs}
        timestamps: dict[int, int] = {}

        for block_num in block_numbers:
            block = await self._w3.eth.get_block(block_num)
            timestamps[block_num] = block["timestamp"]

        return timestamps
