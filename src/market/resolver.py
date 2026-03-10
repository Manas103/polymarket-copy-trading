"""Market resolver: maps token_id to market metadata and orderbook data."""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from config import AppConfig
from src.market.cache import TTLCache
from src.models.market import MarketInfo, OrderBookSnapshot

logger = logging.getLogger(__name__)


class MarketResolver:
    """Resolves token IDs to market info via Gamma API and fetches orderbooks via CLOB."""

    def __init__(self, config: AppConfig) -> None:
        self._gamma_url = config.gamma.api_url
        self._clob_url = config.clob.api_url
        self._cache: TTLCache[MarketInfo] = TTLCache(config.gamma.cache_ttl_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def resolve_market(self, token_id: str) -> Optional[MarketInfo]:
        """Look up market info for a token_id, using cache."""
        cached = self._cache.get(token_id)
        if cached is not None:
            return cached

        info = await self._fetch_from_gamma(token_id)
        if info:
            self._cache.set(token_id, info)
        return info

    async def get_orderbook(self, token_id: str) -> Optional[OrderBookSnapshot]:
        """Fetch current orderbook from CLOB API."""
        if not self._session:
            return None

        try:
            url = f"{self._clob_url}/book"
            params = {"token_id": token_id}
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Orderbook fetch failed: status %d", resp.status)
                    return None
                data = await resp.json()
                return self._parse_orderbook(token_id, data)
        except Exception:
            logger.exception("Error fetching orderbook for %s", token_id)
            return None

    async def _fetch_from_gamma(self, token_id: str) -> Optional[MarketInfo]:
        """Fetch market metadata from Gamma API."""
        if not self._session:
            return None

        try:
            url = f"{self._gamma_url}/markets"
            params = {"clob_token_ids": token_id}
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Gamma API failed: status %d", resp.status)
                    return None
                data = await resp.json()
                if not data:
                    return None
                return self._parse_gamma_response(token_id, data[0])
        except Exception:
            logger.exception("Error fetching market info for %s", token_id)
            return None

    def _parse_gamma_response(
        self, token_id: str, data: dict
    ) -> MarketInfo:
        """Parse Gamma API response into MarketInfo."""
        # Determine which outcome this token represents
        tokens = data.get("tokens", [])
        outcome = ""
        for tok in tokens:
            if tok.get("token_id") == token_id:
                outcome = tok.get("outcome", "")
                break

        return MarketInfo(
            condition_id=data.get("condition_id", ""),
            question=data.get("question", ""),
            outcome=outcome,
            token_id=token_id,
            neg_risk=data.get("neg_risk", False),
            active=data.get("active", True),
            end_date=data.get("end_date_iso", ""),
        )

    def _parse_orderbook(
        self, token_id: str, data: dict
    ) -> OrderBookSnapshot:
        """Parse CLOB orderbook response with full depth."""
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        best_bid = float(bids[0]["price"]) if bids else None
        best_ask = float(asks[0]["price"]) if asks else None
        bid_size = float(bids[0]["size"]) if bids else 0.0
        ask_size = float(asks[0]["size"]) if asks else 0.0

        ask_levels = [(float(a["price"]), float(a["size"])) for a in asks]
        bid_levels = [(float(b["price"]), float(b["size"])) for b in bids]

        return OrderBookSnapshot(
            token_id=token_id,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=bid_size,
            ask_size=ask_size,
            ask_levels=ask_levels,
            bid_levels=bid_levels,
        )
