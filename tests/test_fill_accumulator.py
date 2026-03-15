"""Tests for FillAccumulator: aggregate whale fills per token."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from config import AppConfig, TradingConfig
from src.signal.fill_accumulator import FillAccumulator


class TestFillAccumulator:
    def setup_method(self):
        self.config = AppConfig(
            trading=TradingConfig(
                fill_accumulator_enabled=True,
                fill_accumulator_window_seconds=1800,
                fill_accumulator_cooldown_seconds=3600,
            )
        )
        self.acc = FillAccumulator(self.config)

    @pytest.mark.asyncio
    async def test_single_fill(self):
        """Single fill returns that amount."""
        await self.acc.record_fill("0xWHALE1", "token_1", 500.0)
        result = self.acc.get_aggregate("0xWHALE1", "token_1")
        assert result.aggregate_usd == pytest.approx(500.0)
        assert result.already_fired is False
        assert result.fill_count == 1

    @pytest.mark.asyncio
    async def test_multiple_fills_accumulate(self):
        """Multiple fills on same (whale, token) accumulate."""
        await self.acc.record_fill("0xWHALE1", "token_1", 500.0)
        await self.acc.record_fill("0xWHALE1", "token_1", 1000.0)
        await self.acc.record_fill("0xWHALE1", "token_1", 2500.0)
        result = self.acc.get_aggregate("0xWHALE1", "token_1")
        assert result.aggregate_usd == pytest.approx(4000.0)
        assert result.fill_count == 3

    @pytest.mark.asyncio
    async def test_expired_fills_pruned(self):
        """Fills outside the window are dropped."""
        await self.acc.record_fill("0xWHALE1", "token_1", 500.0)

        with patch("src.signal.fill_accumulator.time") as mock_time:
            mock_time.time.return_value = time.time() + 1801
            await self.acc.record_fill("0xWHALE1", "token_1", 200.0)
            result = self.acc.get_aggregate("0xWHALE1", "token_1")
            assert result.aggregate_usd == pytest.approx(200.0)

    @pytest.mark.asyncio
    async def test_mark_fired_prevents_duplicates(self):
        """After firing, already_fired is True."""
        await self.acc.record_fill("0xWHALE1", "token_1", 500.0)
        await self.acc.mark_fired("0xWHALE1", "token_1")
        result = self.acc.get_aggregate("0xWHALE1", "token_1")
        assert result.already_fired is True

    @pytest.mark.asyncio
    async def test_fired_cooldown_expires(self):
        """After cooldown, already_fired reverts to False."""
        await self.acc.record_fill("0xWHALE1", "token_1", 500.0)
        await self.acc.mark_fired("0xWHALE1", "token_1")

        with patch("src.signal.fill_accumulator.time") as mock_time:
            mock_time.time.return_value = time.time() + 3601
            result = self.acc.get_aggregate("0xWHALE1", "token_1")
            assert result.already_fired is False

    @pytest.mark.asyncio
    async def test_different_whale_token_pairs_independent(self):
        """Different (whale, token) pairs don't interfere."""
        await self.acc.record_fill("0xWHALE1", "token_1", 500.0)
        await self.acc.record_fill("0xWHALE2", "token_2", 1000.0)

        r1 = self.acc.get_aggregate("0xWHALE1", "token_1")
        r2 = self.acc.get_aggregate("0xWHALE2", "token_2")
        assert r1.aggregate_usd == pytest.approx(500.0)
        assert r2.aggregate_usd == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_same_whale_different_tokens_independent(self):
        """Same whale on different tokens tracked separately."""
        await self.acc.record_fill("0xWHALE1", "token_1", 500.0)
        await self.acc.record_fill("0xWHALE1", "token_2", 1000.0)

        r1 = self.acc.get_aggregate("0xWHALE1", "token_1")
        r2 = self.acc.get_aggregate("0xWHALE1", "token_2")
        assert r1.aggregate_usd == pytest.approx(500.0)
        assert r2.aggregate_usd == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_case_insensitive_whale_address(self):
        """Whale address matching is case-insensitive."""
        await self.acc.record_fill("0xAAAA", "token_1", 500.0)
        await self.acc.record_fill("0xaaaa", "token_1", 300.0)
        result = self.acc.get_aggregate("0xAaAa", "token_1")
        assert result.aggregate_usd == pytest.approx(800.0)

    @pytest.mark.asyncio
    async def test_empty_aggregate(self):
        """No fills returns zero."""
        result = self.acc.get_aggregate("0xWHALE1", "token_1")
        assert result.aggregate_usd == pytest.approx(0.0)
        assert result.already_fired is False
        assert result.fill_count == 0
