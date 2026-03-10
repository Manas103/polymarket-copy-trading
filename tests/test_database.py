"""Tests for Database and Repository: schema, CRUD, dedup."""

from __future__ import annotations

import time
from datetime import date

import pytest
import pytest_asyncio

from src.models.events import ExchangeType, OrderFilledEvent
from src.models.signals import SignalAction, TradeSignal
from src.models.trades import CopyTrade, TradeResult, TradeStatus
from src.persistence.database import Database
from src.persistence.repository import Repository
from tests.conftest import WHALE_ADDRESS, NON_WHALE_ADDRESS, make_event


class TestDatabase:
    @pytest.mark.asyncio
    async def test_connect_creates_schema(self, db: Database):
        """Schema tables should exist after connect."""
        cursor = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row["name"] for row in await cursor.fetchall()}
        assert "block_cursor" in tables
        assert "whale_events" in tables
        assert "trade_signals" in tables
        assert "copy_trades" in tables
        assert "daily_risk" in tables
        assert "positions" in tables

    @pytest.mark.asyncio
    async def test_indexes_created(self, db: Database):
        """New indexes should exist after connect."""
        cursor = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        indexes = {row["name"] for row in await cursor.fetchall()}
        assert "idx_whale_events_maker_ts" in indexes
        assert "idx_whale_events_taker_ts" in indexes
        assert "idx_whale_events_block" in indexes


class TestRepository:
    @pytest.mark.asyncio
    async def test_block_cursor_initial(self, repo: Repository):
        assert await repo.get_last_block() is None

    @pytest.mark.asyncio
    async def test_block_cursor_set_get(self, repo: Repository):
        await repo.set_last_block(12345)
        assert await repo.get_last_block() == 12345

    @pytest.mark.asyncio
    async def test_block_cursor_update(self, repo: Repository):
        await repo.set_last_block(100)
        await repo.set_last_block(200)
        assert await repo.get_last_block() == 200

    @pytest.mark.asyncio
    async def test_insert_event(self, repo: Repository):
        event = make_event()
        inserted = await repo.insert_event(event)
        assert inserted is True

    @pytest.mark.asyncio
    async def test_insert_event_dedup(self, repo: Repository):
        event = make_event()
        assert await repo.insert_event(event) is True
        assert await repo.insert_event(event) is False  # Duplicate

    @pytest.mark.asyncio
    async def test_event_exists(self, repo: Repository):
        event = make_event()
        assert await repo.event_exists(event.dedup_key) is False
        await repo.insert_event(event)
        assert await repo.event_exists(event.dedup_key) is True

    @pytest.mark.asyncio
    async def test_insert_signal(self, repo: Repository):
        event = make_event()
        await repo.insert_event(event)

        signal = TradeSignal(
            event=event,
            action=SignalAction.COPY_BUY,
            whale_address=WHALE_ADDRESS,
            market_question="Will X happen?",
        )
        signal_id = await repo.insert_signal(signal)
        assert signal_id > 0

    @pytest.mark.asyncio
    async def test_insert_trade_and_update(self, repo: Repository):
        event = make_event()
        await repo.insert_event(event)
        signal = TradeSignal(event=event, action=SignalAction.COPY_BUY)
        signal_id = await repo.insert_signal(signal)

        trade = CopyTrade(
            token_id="12345",
            amount_usd=5.0,
            worst_price=0.55,
            signal_id=signal_id,
        )
        trade_id = await repo.insert_trade(trade)
        assert trade_id > 0

        result = TradeResult(
            status=TradeStatus.FILLED,
            order_id="order_123",
            filled_amount=9.5,
            filled_price=0.52,
        )
        await repo.update_trade_result(trade_id, result)

        # Verify update
        cursor = await repo.conn.execute(
            "SELECT status, order_id, filled_amount FROM copy_trades WHERE id = ?",
            (trade_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "FILLED"
        assert row["order_id"] == "order_123"
        assert row["filled_amount"] == 9.5

    @pytest.mark.asyncio
    async def test_daily_risk(self, repo: Repository):
        count, spend = await repo.get_daily_risk()
        assert count == 0
        assert spend == 0.0

        await repo.record_daily_trade(5.0)
        await repo.record_daily_trade(3.0)

        count, spend = await repo.get_daily_risk()
        assert count == 2
        assert spend == pytest.approx(8.0)

    @pytest.mark.asyncio
    async def test_positions(self, repo: Repository):
        invested, tokens = await repo.get_position("token_1")
        assert invested == 0.0

        await repo.upsert_position("token_1", "cond_1", 5.0, 10.0)
        invested, tokens = await repo.get_position("token_1")
        assert invested == 5.0
        assert tokens == 10.0

        # Accumulate
        await repo.upsert_position("token_1", "cond_1", 3.0, 6.0)
        invested, tokens = await repo.get_position("token_1")
        assert invested == 8.0
        assert tokens == 16.0

    @pytest.mark.asyncio
    async def test_position_by_condition(self, repo: Repository):
        await repo.upsert_position("token_1", "cond_1", 5.0, 10.0)
        await repo.upsert_position("token_2", "cond_1", 3.0, 6.0)

        total = await repo.get_position_by_condition("cond_1")
        assert total == pytest.approx(8.0)

    @pytest.mark.asyncio
    async def test_count_open_positions(self, repo: Repository):
        assert await repo.count_open_positions() == 0

        await repo.upsert_position("token_1", "cond_1", 5.0, 10.0)
        await repo.upsert_position("token_2", "cond_2", 3.0, 6.0)
        assert await repo.count_open_positions() == 2

    @pytest.mark.asyncio
    async def test_reduce_position(self, repo: Repository):
        """Reduce position by sold amounts, floored at zero."""
        await repo.upsert_position("token_1", "cond_1", 10.0, 20.0)

        await repo.reduce_position("token_1", 3.0, 5.0)
        invested, tokens = await repo.get_position("token_1")
        assert invested == pytest.approx(7.0)
        assert tokens == pytest.approx(15.0)

        # Reduce beyond zero — should floor at 0
        await repo.reduce_position("token_1", 100.0, 100.0)
        invested, tokens = await repo.get_position("token_1")
        assert invested == 0.0
        assert tokens == 0.0

    @pytest.mark.asyncio
    async def test_close_position(self, repo: Repository):
        """Close position zeroes out invested and tokens."""
        await repo.upsert_position("token_1", "cond_1", 10.0, 20.0)

        await repo.close_position("token_1")
        invested, tokens = await repo.get_position("token_1")
        assert invested == 0.0
        assert tokens == 0.0

    # -- New repository query tests --

    @pytest.mark.asyncio
    async def test_get_recent_dedup_keys(self, repo: Repository):
        """Load dedup keys near the last processed block."""
        event1 = make_event(tx_hash="0xrecent1", block_number=900)
        event2 = make_event(tx_hash="0xrecent2", block_number=950)
        event3 = make_event(tx_hash="0xold", block_number=500)
        await repo.insert_event(event1)
        await repo.insert_event(event2)
        await repo.insert_event(event3)

        keys = await repo.get_recent_dedup_keys(1000, safety_margin=200)
        assert event1.dedup_key in keys
        assert event2.dedup_key in keys
        assert event3.dedup_key not in keys

    @pytest.mark.asyncio
    async def test_get_whale_token_activity(self, repo: Repository):
        """Test buy/sell volume for a specific token."""
        now = int(time.time())

        # Buy event: whale as maker, buying token 12345
        buy = make_event(
            tx_hash="0xact_buy",
            block_timestamp=now - 300,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=12345,
            maker_amount_filled=500_000_000,  # $500 USDC
            taker_amount_filled=1_000_000_000,
        )
        await repo.insert_event(buy)

        # Sell event: whale as maker, selling token 12345
        sell = make_event(
            tx_hash="0xact_sell",
            block_timestamp=now - 200,
            maker=WHALE_ADDRESS,
            taker=NON_WHALE_ADDRESS,
            maker_asset_id=12345,
            taker_asset_id=0,
            maker_amount_filled=600_000_000,
            taker_amount_filled=300_000_000,  # $300 USDC
        )
        await repo.insert_event(sell)

        buy_usd, sell_usd = await repo.get_whale_token_activity(
            WHALE_ADDRESS, "12345", now - 3600
        )
        assert buy_usd == pytest.approx(500.0)
        assert sell_usd == pytest.approx(300.0)

    @pytest.mark.asyncio
    async def test_get_whale_token_activity_empty(self, repo: Repository):
        """No activity -> (0, 0)."""
        buy, sell = await repo.get_whale_token_activity(
            WHALE_ADDRESS, "99999", int(time.time()) - 3600
        )
        assert buy == 0.0
        assert sell == 0.0
