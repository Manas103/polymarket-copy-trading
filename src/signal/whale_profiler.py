"""Whale portfolio conviction filter using Polymarket Data API."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from config import AppConfig
from src.market.cache import TTLCache

logger = logging.getLogger(__name__)


@dataclass
class ConvictionResult:
    passed: bool
    conviction_pct: float = 0.0
    portfolio_value: float = 0.0
    reason: str = ""


class WhaleProfiler:
    """Fetches real whale portfolio value and checks trade conviction."""

    def __init__(self, config: AppConfig) -> None:
        self._min_conviction_pct = config.trading.min_conviction_pct
        self._data_api_url = config.gamma.data_api_url
        self._cache: TTLCache[float] = TTLCache(config.trading.portfolio_cache_ttl_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def check_conviction(
        self, whale_address: str, trade_usd: float
    ) -> ConvictionResult:
        """Check if trade represents sufficient conviction relative to whale's portfolio.

        Returns passed=True if:
        - Portfolio API is unavailable (graceful degradation)
        - Whale has no open positions (may be entering fresh)
        - conviction_pct >= min_conviction_pct
        """
        portfolio_value = await self._get_portfolio_value(whale_address)

        if portfolio_value is None:
            return ConvictionResult(
                passed=True,
                reason="Portfolio API unavailable — allowing trade",
            )

        if portfolio_value <= 0:
            return ConvictionResult(
                passed=True,
                reason="No open positions — allowing trade",
            )

        conviction_pct = (trade_usd / portfolio_value) * 100

        if conviction_pct < self._min_conviction_pct:
            reason = (
                f"Low conviction: {conviction_pct:.2f}% < {self._min_conviction_pct}% "
                f"(trade=${trade_usd:.0f}, portfolio=${portfolio_value:.0f})"
            )
            logger.info(reason)
            return ConvictionResult(
                passed=False,
                conviction_pct=conviction_pct,
                portfolio_value=portfolio_value,
                reason=reason,
            )

        return ConvictionResult(
            passed=True,
            conviction_pct=conviction_pct,
            portfolio_value=portfolio_value,
        )

    async def _get_portfolio_value(self, wallet: str) -> float | None:
        """Fetch portfolio value from Polymarket Data API, with caching."""
        key = wallet.lower()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if not self._session:
            return None

        try:
            url = f"{self._data_api_url}/value"
            params = {"user": wallet}
            async with self._session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                value = float(data[0].get("value", 0)) if data else 0.0
                self._cache.set(key, value)
                return value
        except Exception:
            logger.exception("Failed to fetch portfolio value for %s", wallet[:10])
            return None
