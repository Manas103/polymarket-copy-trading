"""Tests for WhaleProfiler: conviction filter using Polymarket Data API."""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from aioresponses import aioresponses

from config import AppConfig, GammaConfig, TradingConfig
from src.signal.whale_profiler import WhaleProfiler
from tests.conftest import WHALE_ADDRESS

DATA_API_URL = "https://data-api.polymarket.com"
VALUE_URL_PATTERN = re.compile(r"^https://data-api\.polymarket\.com/value\?")


@pytest_asyncio.fixture
async def profiler(test_config: AppConfig):
    prof = WhaleProfiler(test_config)
    await prof.start()
    yield prof
    await prof.stop()


class TestWhaleProfiler:
    @pytest.mark.asyncio
    async def test_high_conviction_passes(self, profiler: WhaleProfiler):
        """Large trade relative to portfolio should pass."""
        # Portfolio = $10,000, trade = $200 -> 2% > 1% threshold
        with aioresponses() as m:
            m.get(
                VALUE_URL_PATTERN,
                payload=[{"user": WHALE_ADDRESS, "value": 10000}],
            )
            result = await profiler.check_conviction(WHALE_ADDRESS, 200.0)

        assert result.passed is True
        assert result.conviction_pct == pytest.approx(2.0)
        assert result.portfolio_value == pytest.approx(10000.0)

    @pytest.mark.asyncio
    async def test_low_conviction_rejected(self, profiler: WhaleProfiler):
        """Tiny trade relative to portfolio should be rejected."""
        # Portfolio = $100,000, trade = $100 -> 0.1% < 1% threshold
        with aioresponses() as m:
            m.get(
                VALUE_URL_PATTERN,
                payload=[{"user": WHALE_ADDRESS, "value": 100000}],
            )
            result = await profiler.check_conviction(WHALE_ADDRESS, 100.0)

        assert result.passed is False
        assert result.conviction_pct == pytest.approx(0.1)
        assert "Low conviction" in result.reason

    @pytest.mark.asyncio
    async def test_api_failure_allows_trade(self, profiler: WhaleProfiler):
        """API returning 500 should allow trade (graceful degradation)."""
        with aioresponses() as m:
            m.get(VALUE_URL_PATTERN, status=500)
            result = await profiler.check_conviction(WHALE_ADDRESS, 100.0)

        assert result.passed is True
        assert "unavailable" in result.reason

    @pytest.mark.asyncio
    async def test_zero_portfolio_allows_trade(self, profiler: WhaleProfiler):
        """Whale with zero portfolio should be allowed (entering fresh)."""
        with aioresponses() as m:
            m.get(
                VALUE_URL_PATTERN,
                payload=[{"user": WHALE_ADDRESS, "value": 0}],
            )
            result = await profiler.check_conviction(WHALE_ADDRESS, 500.0)

        assert result.passed is True
        assert "No open positions" in result.reason

    @pytest.mark.asyncio
    async def test_caching(self, profiler: WhaleProfiler):
        """Second call should use cache, not make another HTTP request."""
        with aioresponses() as m:
            m.get(
                VALUE_URL_PATTERN,
                payload=[{"user": WHALE_ADDRESS, "value": 10000}],
            )
            await profiler.check_conviction(WHALE_ADDRESS, 200.0)
            # Second call — no mock registered, would fail if cache miss
            result = await profiler.check_conviction(WHALE_ADDRESS, 200.0)

        assert result.passed is True
        assert result.conviction_pct == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_custom_threshold(self):
        """Test with a custom min_conviction_pct of 5%."""
        config = AppConfig(
            trading=TradingConfig(min_conviction_pct=5.0),
            gamma=GammaConfig(data_api_url=DATA_API_URL),
        )
        prof = WhaleProfiler(config)
        await prof.start()
        try:
            with aioresponses() as m:
                # Portfolio = $10,000, trade = $200 -> 2% < 5% threshold
                m.get(
                    VALUE_URL_PATTERN,
                    payload=[{"user": WHALE_ADDRESS, "value": 10000}],
                )
                result = await prof.check_conviction(WHALE_ADDRESS, 200.0)

            assert result.passed is False
            assert result.conviction_pct == pytest.approx(2.0)
        finally:
            await prof.stop()
