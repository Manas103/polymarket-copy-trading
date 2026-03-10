"""Tests for SignalGenerator: whale detection, buy/sell classification."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from config import AppConfig
from src.models.events import ExchangeType
from src.models.signals import SignalAction
from src.persistence.repository import Repository
from src.signal.generator import SignalGenerator
from tests.conftest import (
    CTF_EXCHANGE_ADDR,
    NON_WHALE_ADDRESS,
    WHALE_ADDRESS,
    make_event,
)


class TestSignalGenerator:
    def setup_method(self):
        self.config = AppConfig(
            whales=pytest.importorskip("config").WhaleConfig(
                addresses=[WHALE_ADDRESS]
            ),
            trading=pytest.importorskip("config").TradingConfig(
                min_whale_trade_usd=100.0,
            ),
        )
        self.gen = SignalGenerator(self.config)

    @pytest.mark.asyncio
    async def test_whale_maker_buy(self):
        """Whale is maker, maker_asset_id=0 -> whale BUY."""
        event = make_event(
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=12345,
            maker_amount_filled=500_000_000,  # $500
        )
        signals = await self.gen.process_events([event])
        assert len(signals) == 1
        assert signals[0].action == SignalAction.COPY_BUY
        assert signals[0].whale_address == WHALE_ADDRESS

    @pytest.mark.asyncio
    async def test_whale_taker_buy(self):
        """Whale is taker, taker_asset_id=0 -> whale BUY."""
        event = make_event(
            maker=NON_WHALE_ADDRESS,
            taker=WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
            taker_amount_filled=500_000_000,  # $500
        )
        signals = await self.gen.process_events([event])
        assert len(signals) == 1
        assert signals[0].action == SignalAction.COPY_BUY
        assert signals[0].whale_address == WHALE_ADDRESS

    @pytest.mark.asyncio
    async def test_whale_maker_sell(self):
        """Whale is maker, maker_asset_id != 0 -> whale SELL -> skip (no repo)."""
        event = make_event(
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
        )
        signals = await self.gen.process_events([event])
        assert signals[0].action == SignalAction.SKIP_SELL

    @pytest.mark.asyncio
    async def test_whale_taker_sell(self):
        """Whale is taker, taker_asset_id != 0 -> whale SELL -> skip (no repo)."""
        event = make_event(
            maker=NON_WHALE_ADDRESS,
            taker=WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=12345,
        )
        signals = await self.gen.process_events([event])
        assert signals[0].action == SignalAction.SKIP_SELL

    @pytest.mark.asyncio
    async def test_non_whale_skip(self):
        event = make_event(
            maker=NON_WHALE_ADDRESS,
            taker="0x2222222222222222222222222222222222222222",
        )
        signals = await self.gen.process_events([event])
        assert signals[0].action == SignalAction.SKIP_NOT_WHALE

    @pytest.mark.asyncio
    async def test_below_threshold_skip(self):
        """Whale buy but below $100 threshold."""
        event = make_event(
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=0,
            maker_amount_filled=50_000_000,  # $50
        )
        signals = await self.gen.process_events([event])
        assert signals[0].action == SignalAction.SKIP_BELOW_THRESHOLD

    @pytest.mark.asyncio
    async def test_whale_address_case_insensitive(self):
        """Whale matching should be case-insensitive."""
        event = make_event(
            maker=WHALE_ADDRESS.lower(),
            maker_asset_id=0,
            maker_amount_filled=500_000_000,
        )
        signals = await self.gen.process_events([event])
        assert signals[0].action == SignalAction.COPY_BUY

    @pytest.mark.asyncio
    async def test_whale_price_estimation(self):
        """Check price estimation from amounts."""
        event = make_event(
            maker=WHALE_ADDRESS,
            maker_asset_id=0,
            maker_amount_filled=500_000_000,  # $500 USDC
            taker_amount_filled=1_000_000_000,  # 1000 tokens
        )
        signals = await self.gen.process_events([event])
        assert signals[0].whale_price == pytest.approx(0.5)

    # --- Sell with/without position tests ---

    @pytest.mark.asyncio
    async def test_sell_with_position_emits_copy_sell(self):
        """Whale sell + we hold a position -> COPY_SELL."""
        repo = AsyncMock(spec=Repository)
        repo.get_position.return_value = (10.0, 20.0)  # invested, tokens
        gen = SignalGenerator(self.config, repository=repo)

        event = make_event(
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
        )
        signals = await gen.process_events([event])
        assert signals[0].action == SignalAction.COPY_SELL
        assert signals[0].whale_address == WHALE_ADDRESS

    @pytest.mark.asyncio
    async def test_sell_without_position_emits_skip_sell(self):
        """Whale sell + no position -> SKIP_SELL."""
        repo = AsyncMock(spec=Repository)
        repo.get_position.return_value = (0.0, 0.0)
        gen = SignalGenerator(self.config, repository=repo)

        event = make_event(
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
        )
        signals = await gen.process_events([event])
        assert signals[0].action == SignalAction.SKIP_SELL

    @pytest.mark.asyncio
    async def test_sell_without_repo_emits_skip_sell(self):
        """Whale sell + no repo (backward compat) -> SKIP_SELL."""
        gen = SignalGenerator(self.config)  # No repo

        event = make_event(
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
        )
        signals = await gen.process_events([event])
        assert signals[0].action == SignalAction.SKIP_SELL
