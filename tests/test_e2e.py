"""End-to-end tests with all mocks: simulate events through full pipeline."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from config import AppConfig
from src.executor.trade_executor import TradeExecutor
from src.market.resolver import MarketResolver
from src.models.events import ExchangeType
from src.models.market import MarketInfo, OrderBookSnapshot
from src.models.trades import CopyTrade, TradeResult, TradeStatus
from src.monitor.blockchain import BlockchainMonitor
from src.monitor.event_parser import EventParser
from src.persistence.database import Database
from src.persistence.repository import Repository
from src.pipeline import TradingPipeline
from src.risk.manager import RiskManager
from src.signal.filter import TradeFilter
from src.signal.generator import SignalGenerator
from src.signal.whale_activity_tracker import WhaleActivityTracker
from src.signal.whale_profiler import ConvictionResult, WhaleProfiler
from tests.conftest import WHALE_ADDRESS, make_event


class TestEndToEnd:
    @pytest_asyncio.fixture
    async def setup(self, db: Database, test_config: AppConfig):
        repo = Repository(db)

        monitor = MagicMock(spec=BlockchainMonitor)
        monitor._last_block = 5000

        parser = EventParser()
        signal_gen = SignalGenerator(test_config, repository=repo)
        trade_filter = TradeFilter(test_config)

        resolver = AsyncMock(spec=MarketResolver)
        resolver.resolve_market.return_value = MarketInfo(
            condition_id="cond_e2e",
            question="E2E test market",
            outcome="Yes",
            token_id="999",
        )
        resolver.get_orderbook.return_value = OrderBookSnapshot(
            token_id="999",
            best_bid=0.49,
            best_ask=0.509,
            bid_size=200.0,
            ask_size=200.0,
        )

        risk_manager = RiskManager(test_config, repo)

        executor = MagicMock(spec=TradeExecutor)
        executor.build_copy_trade.return_value = CopyTrade(
            token_id="999", amount_usd=5.0, worst_price=0.55, signal_id=1
        )
        executor.execute = AsyncMock(
            return_value=TradeResult(
                status=TradeStatus.FILLED,
                order_id="e2e_order_001",
                filled_amount=9.6,
                filled_price=0.52,
            )
        )
        executor.calculate_copy_amount = MagicMock(return_value=5.0)

        whale_profiler = AsyncMock(spec=WhaleProfiler)
        whale_profiler.check_conviction.return_value = ConvictionResult(passed=True)
        activity_tracker = WhaleActivityTracker(test_config, repo)

        pipeline = TradingPipeline(
            config=test_config,
            monitor=monitor,
            parser=parser,
            signal_gen=signal_gen,
            trade_filter=trade_filter,
            market_resolver=resolver,
            risk_manager=risk_manager,
            executor=executor,
            repository=repo,
            whale_profiler=whale_profiler,
            activity_tracker=activity_tracker,
        )

        return pipeline, repo, executor, resolver

    @pytest.mark.asyncio
    async def test_whale_buy_flows_through(self, setup):
        """Simulate a whale buy flowing through the entire pipeline."""
        pipeline, repo, executor, resolver = setup

        now = int(time.time())
        # Simulate whale buy event with recent timestamp
        # Price = maker_amount / taker_amount = 500M / 1000M = 0.50
        event = make_event(
            tx_hash="0xe2e_buy_1",
            block_timestamp=now - 2,
            maker=WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=999,
            maker_amount_filled=500_000_000,  # $500
            taker_amount_filled=1_000_000_000,
        )

        # Process events through signal generation
        signals = await pipeline._signal_gen.process_events([event])
        assert len(signals) == 1
        assert signals[0].should_copy is True

        # Persist
        await repo.insert_event(event)
        signal_id = await repo.insert_signal(signals[0])

        # Filter
        orderbook = await resolver.get_orderbook("999")
        filter_result = pipeline._filter.check(signals[0], orderbook)
        assert filter_result.passed is True

        # Risk check
        risk_check = await pipeline._risk.check_trade(5.0, "999", "cond_e2e")
        assert risk_check.allowed is True

        # Execute
        trade = executor.build_copy_trade(signals[0], orderbook, signal_id)
        trade_id = await repo.insert_trade(trade)
        result = await executor.execute(trade)
        await repo.update_trade_result(trade_id, result)

        # Verify result
        assert result.status == TradeStatus.FILLED
        assert result.order_id == "e2e_order_001"

        # Verify persistence
        cursor = await repo.conn.execute(
            "SELECT status FROM copy_trades WHERE id = ?", (trade_id,)
        )
        row = await cursor.fetchone()
        assert row["status"] == "FILLED"

    @pytest.mark.asyncio
    async def test_duplicate_events_rejected(self, setup):
        """Same event should not be processed twice."""
        pipeline, repo, *_ = setup

        event = make_event(tx_hash="0xdup_test", log_index=0)

        assert await repo.insert_event(event) is True
        assert await repo.insert_event(event) is False

    @pytest.mark.asyncio
    async def test_block_cursor_resume(self, setup):
        """Pipeline should resume from saved block cursor."""
        pipeline, repo, *_ = setup

        await repo.set_last_block(42000)
        last = await repo.get_last_block()
        assert last == 42000

    @pytest.mark.asyncio
    async def test_multiple_signals_mixed(self, setup):
        """Mix of buy/sell/non-whale events."""
        pipeline, repo, *_ = setup

        events = [
            # Whale buy -> should copy
            make_event(
                tx_hash="0xmix_1", log_index=0,
                maker=WHALE_ADDRESS, maker_asset_id=0,
                maker_amount_filled=500_000_000,
            ),
            # Whale sell -> skip (no position)
            make_event(
                tx_hash="0xmix_2", log_index=0,
                maker=WHALE_ADDRESS, maker_asset_id=12345, taker_asset_id=0,
            ),
            # Non-whale -> skip
            make_event(
                tx_hash="0xmix_3", log_index=0,
                maker="0x5555555555555555555555555555555555555555",
            ),
        ]

        signals = await pipeline._signal_gen.process_events(events)
        assert len(signals) == 3

        copy_signals = [s for s in signals if s.should_copy]
        assert len(copy_signals) == 1
        assert copy_signals[0].whale_address == WHALE_ADDRESS

    @pytest.mark.asyncio
    async def test_daily_risk_accumulation(self, setup):
        """Risk limits should accumulate across trades."""
        pipeline, repo, *_ = setup

        # Record trades
        for i in range(3):
            await repo.record_daily_trade(5.0)

        count, spend = await repo.get_daily_risk()
        assert count == 3
        assert spend == pytest.approx(15.0)
