"""Tests for WhaleActivityTracker: sell-to-buy ratio detection."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio

from config import AppConfig, TradingConfig
from src.persistence.database import Database
from src.persistence.repository import Repository
from src.signal.whale_activity_tracker import WhaleActivityTracker
from tests.conftest import WHALE_ADDRESS, NON_WHALE_ADDRESS, make_event


class TestWhaleActivityTracker:
    @pytest_asyncio.fixture
    async def tracker(self, db: Database, test_config: AppConfig):
        repo = Repository(db)
        return WhaleActivityTracker(test_config, repo), repo

    @pytest.mark.asyncio
    async def test_no_activity_passes(self, tracker):
        """No recent activity -> passes."""
        trk, repo = tracker
        result = await trk.check_activity(WHALE_ADDRESS, "12345")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_only_buys_passes(self, tracker):
        """Only buy activity -> passes (no sells)."""
        trk, repo = tracker
        now = int(time.time())

        # Whale as maker buying token 12345
        event = make_event(
            tx_hash="0xbuy01",
            block_timestamp=now - 300,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=12345,
            maker_amount_filled=500_000_000,  # $500 USDC
            taker_amount_filled=1_000_000_000,
        )
        await repo.insert_event(event)

        result = await trk.check_activity(WHALE_ADDRESS, "12345")
        assert result.passed is True
        assert result.buy_usd > 0
        assert result.sell_usd == 0

    @pytest.mark.asyncio
    async def test_heavy_selling_rejected(self, tracker):
        """Sells significantly exceed buys -> rejected."""
        trk, repo = tracker
        now = int(time.time())

        # Whale buys $200
        buy_event = make_event(
            tx_hash="0xbuy02",
            block_timestamp=now - 600,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=12345,
            maker_amount_filled=200_000_000,
            taker_amount_filled=400_000_000,
        )
        await repo.insert_event(buy_event)

        # Whale sells $500 (maker pays tokens, receives USDC)
        sell_event = make_event(
            tx_hash="0xsell01",
            block_timestamp=now - 300,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount_filled=1_000_000_000,
            taker_amount_filled=500_000_000,  # $500 USDC
        )
        await repo.insert_event(sell_event)

        result = await trk.check_activity(WHALE_ADDRESS, "12345")
        assert result.passed is False
        assert result.ratio > 1.5
        assert "net-exiting" in result.reason

    @pytest.mark.asyncio
    async def test_balanced_activity_passes(self, tracker):
        """Roughly balanced buy/sell -> passes."""
        trk, repo = tracker
        now = int(time.time())

        # Buy $500
        buy = make_event(
            tx_hash="0xbal_buy",
            block_timestamp=now - 600,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=12345,
            maker_amount_filled=500_000_000,
            taker_amount_filled=1_000_000_000,
        )
        await repo.insert_event(buy)

        # Sell $400 (ratio = 0.8 < 1.5)
        sell = make_event(
            tx_hash="0xbal_sell",
            block_timestamp=now - 300,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount_filled=800_000_000,
            taker_amount_filled=400_000_000,
        )
        await repo.insert_event(sell)

        result = await trk.check_activity(WHALE_ADDRESS, "12345")
        assert result.passed is True
        assert result.ratio <= 1.5

    @pytest.mark.asyncio
    async def test_only_sells_rejected(self, tracker):
        """Only sell activity with zero buys -> rejected."""
        trk, repo = tracker
        now = int(time.time())

        sell = make_event(
            tx_hash="0xsellonly",
            block_timestamp=now - 300,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount_filled=1_000_000_000,
            taker_amount_filled=500_000_000,
        )
        await repo.insert_event(sell)

        result = await trk.check_activity(WHALE_ADDRESS, "12345")
        assert result.passed is False
        assert "exiting" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_old_activity_ignored(self, tracker):
        """Activity outside the window should be ignored."""
        trk, repo = tracker
        now = int(time.time())

        # Sell event from 10 hours ago (outside 4h window)
        old_sell = make_event(
            tx_hash="0xold_sell",
            block_timestamp=now - 36000,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount_filled=1_000_000_000,
            taker_amount_filled=500_000_000,
        )
        await repo.insert_event(old_sell)

        result = await trk.check_activity(WHALE_ADDRESS, "12345")
        assert result.passed is True
