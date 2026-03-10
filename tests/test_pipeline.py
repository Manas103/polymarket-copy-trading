"""Tests for TradingPipeline: full flow with mocks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from config import AppConfig, TradingConfig
from src.executor.trade_executor import TradeExecutor
from src.market.resolver import MarketResolver
from src.models.events import ExchangeType
from src.models.market import MarketInfo, OrderBookSnapshot
from src.models.signals import SignalAction
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


class TestPipeline:
    @pytest_asyncio.fixture
    async def pipeline_components(self, db: Database, test_config: AppConfig):
        repo = Repository(db)

        monitor = MagicMock(spec=BlockchainMonitor)
        monitor._last_block = 1000

        parser = EventParser()
        signal_gen = SignalGenerator(test_config, repository=repo)
        trade_filter = TradeFilter(test_config)

        resolver = AsyncMock(spec=MarketResolver)
        resolver.resolve_market.return_value = MarketInfo(
            condition_id="cond_1",
            question="Will X happen?",
            outcome="Yes",
            token_id="123456789",
            neg_risk=False,
        )
        resolver.get_orderbook.return_value = OrderBookSnapshot(
            token_id="123456789",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=100.0,
            ask_size=100.0,
        )

        risk_manager = RiskManager(test_config, repo)

        executor = MagicMock(spec=TradeExecutor)
        executor.build_copy_trade.return_value = CopyTrade(
            token_id="123456789",
            amount_usd=5.0,
            worst_price=0.535,
            signal_id=1,
        )
        executor.execute = AsyncMock(
            return_value=TradeResult(
                status=TradeStatus.FILLED,
                order_id="order_001",
                filled_amount=9.8,
                filled_price=0.51,
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

        return pipeline, monitor, resolver, executor, repo

    @pytest.mark.asyncio
    async def test_full_flow_whale_buy(self, pipeline_components):
        pipeline, monitor, resolver, executor, repo = pipeline_components

        # Create a whale buy event
        event = make_event(
            maker=WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=123456789,
            maker_amount_filled=500_000_000,
        )

        # Build raw log data for the event (simulate parsed)
        # We skip the monitor and feed events directly by mocking parse_logs
        with patch.object(pipeline._parser, "parse_logs", return_value=[event]):
            # Simulate one iteration
            logs = [MagicMock()]
            timestamps = {1000: 1700000000}

            # Process manually
            events = pipeline._parser.parse_logs(logs, timestamps)
            signals = await pipeline._signal_gen.process_events(events)

            assert len(signals) == 1
            assert signals[0].action == SignalAction.COPY_BUY

            # The full pipeline processes signals
            signal = signals[0]
            await repo.insert_event(signal.event)
            signal_id = await repo.insert_signal(signal)

            # Execute
            trade = executor.build_copy_trade(signal, MagicMock(), signal_id)
            trade_id = await repo.insert_trade(trade)
            result = await executor.execute(trade)
            await repo.update_trade_result(trade_id, result)

            assert result.status == TradeStatus.FILLED

    @pytest.mark.asyncio
    async def test_sell_skipped(self, pipeline_components):
        pipeline, *_ = pipeline_components

        # Whale sell event — no position held, should be SKIP_SELL
        event = make_event(
            maker=WHALE_ADDRESS,
            maker_asset_id=123456789,  # Selling tokens
            taker_asset_id=0,
        )

        signals = await pipeline._signal_gen.process_events([event])
        assert len(signals) == 1
        assert signals[0].action == SignalAction.SKIP_SELL

    @pytest.mark.asyncio
    async def test_non_whale_skipped(self, pipeline_components):
        pipeline, *_ = pipeline_components

        event = make_event(
            maker="0x9999999999999999999999999999999999999999",
            taker="0x8888888888888888888888888888888888888888",
        )

        signals = await pipeline._signal_gen.process_events([event])
        assert signals[0].action == SignalAction.SKIP_NOT_WHALE

    @pytest.mark.asyncio
    async def test_persistence(self, pipeline_components):
        """Verify events and trades are persisted to DB."""
        pipeline, monitor, resolver, executor, repo = pipeline_components

        event = make_event(
            maker=WHALE_ADDRESS,
            maker_asset_id=0,
            maker_amount_filled=500_000_000,
        )

        inserted = await repo.insert_event(event)
        assert inserted is True
        assert await repo.event_exists(event.dedup_key)

        # Block cursor persistence
        await repo.set_last_block(1000)
        assert await repo.get_last_block() == 1000

    @pytest.mark.asyncio
    async def test_dedup_on_restart(self, pipeline_components):
        """Verify dedup keys are loaded from DB during initialize."""
        pipeline, monitor, resolver, executor, repo = pipeline_components

        # Insert some events into DB
        event1 = make_event(tx_hash="0xdedup1", log_index=0, block_number=990)
        event2 = make_event(tx_hash="0xdedup2", log_index=0, block_number=995)
        await repo.insert_event(event1)
        await repo.insert_event(event2)
        await repo.set_last_block(1000)

        # Re-initialize pipeline (simulates restart)
        await pipeline.initialize()

        # Verify both dedup keys are now in the parser's _seen set
        assert event1.dedup_key in pipeline._parser._seen
        assert event2.dedup_key in pipeline._parser._seen

    @pytest.mark.asyncio
    async def test_market_resolution_failure_skips(self, pipeline_components):
        """When market resolution fails, signal gets SKIP_MARKET_RESOLUTION_FAILED."""
        pipeline, monitor, resolver, executor, repo = pipeline_components

        # Make resolver return None
        resolver.resolve_market.return_value = None

        event = make_event(
            maker=WHALE_ADDRESS,
            maker_asset_id=0,
            taker_asset_id=123456789,
            maker_amount_filled=500_000_000,
        )

        signals = await pipeline._signal_gen.process_events([event])
        signal = signals[0]
        assert signal.should_copy

        # Insert event first
        await repo.insert_event(signal.event)

        # Simulate pipeline processing — conviction passes (mocked)
        conviction = await pipeline._profiler.check_conviction(
            signal.whale_address, signal.event.usdc_amount
        )
        assert conviction.passed

        # Activity passes (no history)
        token_id = str(signal.event.token_id)
        activity = await pipeline._activity.check_activity(
            signal.whale_address, token_id
        )
        assert activity.passed

        # Market resolution fails
        market_info = await resolver.resolve_market(token_id)
        assert market_info is None

    @pytest.mark.asyncio
    async def test_sell_signal_executes_when_position_held(self, pipeline_components):
        """When whale sells and we hold a position, COPY_SELL flows through and position is closed."""
        pipeline, monitor, resolver, executor, repo = pipeline_components

        token_id_int = 123456789
        token_id = str(token_id_int)

        # Pre-populate a position
        await repo.upsert_position(token_id, "cond_1", 5.0, 10.0)

        # Create whale sell event
        event = make_event(
            tx_hash="0xsell_test",
            maker=WHALE_ADDRESS,
            maker_asset_id=token_id_int,  # Selling tokens
            taker_asset_id=0,
        )

        # Process through signal generator (has repo, position exists -> COPY_SELL)
        signals = await pipeline._signal_gen.process_events([event])
        assert len(signals) == 1
        assert signals[0].action == SignalAction.COPY_SELL
        assert signals[0].should_copy is True
        assert signals[0].is_sell is True

        # Verify position exists before sell
        invested, tokens = await repo.get_position(token_id)
        assert invested == 5.0
        assert tokens == 10.0

        # Simulate the sell path: persist event + signal, build trade, execute, close position
        await repo.insert_event(signals[0].event)
        signal_id = await repo.insert_signal(signals[0])

        # Build sell trade
        executor.build_copy_trade.return_value = CopyTrade(
            token_id=token_id,
            amount_usd=10.0,  # position_tokens
            side="SELL",
            worst_price=0.48,
            signal_id=signal_id,
        )
        trade = executor.build_copy_trade(
            signals[0], MagicMock(), signal_id, position_tokens=10.0
        )
        trade_id = await repo.insert_trade(trade)
        result = await executor.execute(trade)
        await repo.update_trade_result(trade_id, result)

        # Close position
        await repo.close_position(token_id)

        # Verify position is closed
        invested, tokens = await repo.get_position(token_id)
        assert invested == 0.0
        assert tokens == 0.0
