"""Tests for TradeExecutor: order building, execution, result parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import AppConfig, CircuitBreakerConfig, TradingConfig
from src.executor.clob_wrapper import AsyncClobWrapper
from src.executor.trade_executor import TradeExecutor
from src.models.market import OrderBookSnapshot
from src.models.signals import SignalAction, TradeSignal
from src.models.trades import CopyTrade, TradeStatus
from src.risk.circuit_breaker import CircuitBreaker
from tests.conftest import WHALE_ADDRESS, make_event


class TestTradeExecutor:
    def setup_method(self):
        self.config = AppConfig(
            trading=TradingConfig(
                copy_amount_usd=5.0,
                max_slippage_pct=2.0,
                max_copy_amount_usd=25.0,
                conviction_scaling_enabled=True,
                conviction_base_pct=1.0,
                conviction_max_pct=10.0,
            ),
            circuit_breaker=CircuitBreakerConfig(failure_threshold=3),
        )
        self.clob = AsyncMock(spec=AsyncClobWrapper)
        self.cb = CircuitBreaker(self.config.circuit_breaker, name="test")
        self.executor = TradeExecutor(self.config, self.clob, self.cb)

    def test_build_copy_trade(self):
        signal = TradeSignal(
            event=make_event(),
            action=SignalAction.COPY_BUY,
            whale_address=WHALE_ADDRESS,
        )
        ob = OrderBookSnapshot(
            token_id="123", best_bid=0.49, best_ask=0.51, ask_size=100
        )
        trade = self.executor.build_copy_trade(signal, ob, signal_id=1)
        assert trade.amount_usd == 5.0
        assert trade.side == "BUY"
        assert trade.order_type == "FAK"
        assert trade.worst_price == pytest.approx(0.5202)  # 0.51 * 1.02
        assert trade.signal_id == 1

    def test_worst_price_capped(self):
        """Worst price should not exceed 0.99."""
        signal = TradeSignal(
            event=make_event(),
            action=SignalAction.COPY_BUY,
            whale_address=WHALE_ADDRESS,
        )
        ob = OrderBookSnapshot(
            token_id="123", best_bid=0.96, best_ask=0.98, ask_size=100
        )
        trade = self.executor.build_copy_trade(signal, ob, signal_id=1)
        assert trade.worst_price == 0.99  # Capped (0.98 * 1.02 = 0.9996 > 0.99)

    def test_build_copy_sell_trade(self):
        """Sell trade uses best_bid, side=SELL, amount=position_tokens."""
        signal = TradeSignal(
            event=make_event(
                maker=WHALE_ADDRESS,
                maker_asset_id=12345,
                taker_asset_id=0,
            ),
            action=SignalAction.COPY_SELL,
            whale_address=WHALE_ADDRESS,
        )
        ob = OrderBookSnapshot(
            token_id="123", best_bid=0.49, best_ask=0.51, ask_size=100
        )
        trade = self.executor.build_copy_trade(
            signal, ob, signal_id=1, position_tokens=15.0
        )
        assert trade.side == "SELL"
        assert trade.amount_usd == 15.0  # position_tokens
        assert trade.worst_price == pytest.approx(0.4802)  # 0.49 * (1 - 0.02)

    # --- Dynamic sizing tests ---

    def test_calculate_copy_amount_low_conviction(self):
        """At base conviction (1%), amount = copy_amount_usd ($5)."""
        signal = TradeSignal(
            event=make_event(), action=SignalAction.COPY_BUY, conviction_pct=1.0
        )
        amount = self.executor.calculate_copy_amount(signal)
        assert amount == pytest.approx(5.0)

    def test_calculate_copy_amount_mid_conviction(self):
        """At mid conviction (5.5%), amount interpolated between $5 and $25."""
        signal = TradeSignal(
            event=make_event(), action=SignalAction.COPY_BUY, conviction_pct=5.5
        )
        amount = self.executor.calculate_copy_amount(signal)
        assert amount == pytest.approx(15.0)

    def test_calculate_copy_amount_high_conviction(self):
        """At max conviction (10%), amount = max_copy_amount_usd ($25)."""
        signal = TradeSignal(
            event=make_event(), action=SignalAction.COPY_BUY, conviction_pct=10.0
        )
        amount = self.executor.calculate_copy_amount(signal)
        assert amount == pytest.approx(25.0)

    def test_calculate_copy_amount_above_max(self):
        """Above max conviction, amount capped at $25."""
        signal = TradeSignal(
            event=make_event(), action=SignalAction.COPY_BUY, conviction_pct=20.0
        )
        amount = self.executor.calculate_copy_amount(signal)
        assert amount == pytest.approx(25.0)

    def test_calculate_copy_amount_disabled(self):
        """When scaling disabled, always returns base amount."""
        config = AppConfig(
            trading=TradingConfig(
                copy_amount_usd=5.0,
                conviction_scaling_enabled=False,
            ),
            circuit_breaker=CircuitBreakerConfig(failure_threshold=3),
        )
        executor = TradeExecutor(config, self.clob, self.cb)
        signal = TradeSignal(
            event=make_event(), action=SignalAction.COPY_BUY, conviction_pct=10.0
        )
        amount = executor.calculate_copy_amount(signal)
        assert amount == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_execute_filled(self):
        trade = CopyTrade(
            token_id="123", amount_usd=5.0, worst_price=0.55, signal_id=1
        )
        self.clob.create_and_post_market_order.return_value = {
            "status": "matched",
            "orderID": "order_001",
            "matchedAmount": 9.5,
            "avgPrice": 0.52,
        }

        result = await self.executor.execute(trade)
        assert result.status == TradeStatus.FILLED
        assert result.order_id == "order_001"
        assert result.filled_amount == 9.5
        assert result.filled_price == 0.52

    @pytest.mark.asyncio
    async def test_execute_delayed(self):
        trade = CopyTrade(
            token_id="123", amount_usd=5.0, worst_price=0.55, signal_id=1
        )
        self.clob.create_and_post_market_order.return_value = {
            "status": "delayed",
            "orderID": "order_002",
        }

        result = await self.executor.execute(trade)
        assert result.status == TradeStatus.FILLED
        assert result.order_id == "order_002"

    @pytest.mark.asyncio
    async def test_execute_error(self):
        trade = CopyTrade(
            token_id="123", amount_usd=5.0, worst_price=0.55, signal_id=1
        )
        self.clob.create_and_post_market_order.side_effect = Exception("RPC timeout")

        result = await self.executor.execute(trade)
        assert result.status == TradeStatus.ERROR
        assert "RPC timeout" in result.error_message

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks(self):
        """After enough failures, circuit breaker blocks execution."""
        trade = CopyTrade(
            token_id="123", amount_usd=5.0, worst_price=0.55, signal_id=1
        )
        self.clob.create_and_post_market_order.side_effect = Exception("fail")

        # Trigger 3 failures to trip circuit breaker
        for _ in range(3):
            await self.executor.execute(trade)

        # Next call should be blocked
        result = await self.executor.execute(trade)
        assert result.status == TradeStatus.REJECTED
        assert "Circuit breaker" in result.error_message
