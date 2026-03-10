"""Async wrapper around the synchronous py-clob-client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    MarketOrderArgs,
    OrderType,
    PartialCreateOrderOptions,
)

from config import ClobConfig

logger = logging.getLogger(__name__)


class AsyncClobWrapper:
    """Wraps py-clob-client's synchronous methods with asyncio.to_thread."""

    def __init__(self, config: ClobConfig) -> None:
        self._config = config
        self._client: ClobClient | None = None

    async def connect(self) -> None:
        """Initialize the CLOB client (blocking, run in thread)."""
        def _init() -> ClobClient:
            creds = ApiCreds(
                api_key=self._config.api_key,
                api_secret=self._config.api_secret,
                api_passphrase=self._config.api_passphrase,
            )
            kwargs: dict = {
                "key": self._config.private_key,
                "chain_id": self._config.chain_id,
            }
            if self._config.signature_type is not None:
                kwargs["signature_type"] = self._config.signature_type
            client = ClobClient(self._config.api_url, **kwargs)
            client.set_api_creds(creds)
            return client

        self._client = await asyncio.to_thread(_init)
        logger.info("CLOB client connected")

    @property
    def client(self) -> ClobClient:
        if self._client is None:
            raise RuntimeError("CLOB client not connected. Call connect() first.")
        return self._client

    async def create_and_post_market_order(
        self,
        args: MarketOrderArgs,
        order_type: OrderType = OrderType.FAK,
        neg_risk: bool = False,
        tick_size: str = "0.01",
    ) -> dict[str, Any]:
        """Create and submit a market order via CLOB API."""
        def _execute() -> dict[str, Any]:
            options = PartialCreateOrderOptions(
                neg_risk=neg_risk,
                tick_size=tick_size,
            )
            signed_order = self.client.create_market_order(args, options)
            result = self.client.post_order(signed_order, order_type)
            return result

        return await asyncio.to_thread(_execute)

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Get order status."""
        return await asyncio.to_thread(self.client.get_order, order_id)
