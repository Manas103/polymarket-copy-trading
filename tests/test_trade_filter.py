"""Tests for TradeFilter: pre-execution risk and liquidity checks."""

from __future__ import annotations

import time

import pytest

from config import AppConfig, TradingConfig
from src.models.market import MarketInfo, OrderBookSnapshot
from src.models.signals import SignalAction, TradeSignal
from src.signal.filter import TradeFilter
from tests.conftest import WHALE_ADDRESS, make_event


class TestTradeFilter:
    def setup_method(self):
        self.config = AppConfig(
            trading=TradingConfig(max_slippage_pct=5.0),
        )
        self.filter = TradeFilter(self.config)
        # Use current block_timestamp so price movement filter doesn't trigger
        self.signal = TradeSignal(
            event=make_event(block_timestamp=int(time.time())),
            action=SignalAction.COPY_BUY,
            whale_address=WHALE_ADDRESS,
            whale_price=0.50,
        )

    def test_passes_all_checks(self):
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(self.signal, ob)
        assert result.passed is True

    def test_no_orderbook(self):
        result = self.filter.check(self.signal, None)
        assert result.passed is False
        assert "No orderbook" in result.reason

    def test_no_ask_price(self):
        ob = OrderBookSnapshot(token_id="123", best_bid=0.49)
        result = self.filter.check(self.signal, ob)
        assert result.passed is False

    def test_no_ask_liquidity(self):
        ob = OrderBookSnapshot(
            token_id="123", best_bid=0.49, best_ask=0.51, ask_size=0.0
        )
        result = self.filter.check(self.signal, ob)
        assert result.passed is False
        assert "liquidity" in result.reason

    def test_price_too_high(self):
        ob = OrderBookSnapshot(
            token_id="123", best_bid=0.98, best_ask=0.99, ask_size=100.0
        )
        result = self.filter.check(self.signal, ob)
        assert result.passed is False
        assert "too high" in result.reason

    def test_slippage_too_high(self):
        """Ask is 10% above whale price -> exceeds 5% limit."""
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.54,
            best_ask=0.56,  # 12% above 0.50
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(self.signal, ob)
        assert result.passed is False
        assert "Slippage" in result.reason

    def test_slippage_at_limit(self):
        """Ask is just under 3% above whale price -> passes both slippage and price movement."""
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.50,
            best_ask=0.514,  # 2.8% above 0.50 — within both 5% slippage and 3% price movement
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(self.signal, ob)
        assert result.passed is True

    # --- Resolution time filter tests ---

    def test_market_expiring_soon(self):
        """Market ending in 6 hours -> rejected."""
        from datetime import datetime, timedelta, timezone

        end_time = datetime.now(timezone.utc) + timedelta(hours=6)
        market_info = MarketInfo(
            condition_id="cond_1",
            question="Test?",
            outcome="Yes",
            token_id="123",
            end_date=end_time.isoformat(),
        )
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(self.signal, ob, market_info)
        assert result.passed is False
        assert "expires" in result.reason.lower()

    def test_market_far_from_resolution(self):
        """Market ending in 72 hours -> passes."""
        from datetime import datetime, timedelta, timezone

        end_time = datetime.now(timezone.utc) + timedelta(hours=72)
        market_info = MarketInfo(
            condition_id="cond_1",
            question="Test?",
            outcome="Yes",
            token_id="123",
            end_date=end_time.isoformat(),
        )
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(self.signal, ob, market_info)
        assert result.passed is True

    def test_no_end_date_passes(self):
        """Market with no end_date should pass resolution check."""
        market_info = MarketInfo(
            condition_id="cond_1",
            question="Test?",
            outcome="Yes",
            token_id="123",
            end_date="",
        )
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(self.signal, ob, market_info)
        assert result.passed is True

    # --- Orderbook depth filter tests ---

    def test_insufficient_depth_rejected(self):
        """Thin orderbook with levels -> rejected."""
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=5.0,
            ask_size=5.0,
            ask_levels=[(0.51, 5.0), (0.52, 3.0)],  # $2.55 + $1.56 = ~$4.11
        )
        # copy_amount=5, multiplier=2 -> need $10 depth
        result = self.filter.check(self.signal, ob)
        assert result.passed is False
        assert "depth" in result.reason.lower()

    def test_sufficient_depth_passes(self):
        """Deep orderbook -> passes."""
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=100.0,
            ask_size=100.0,
            ask_levels=[(0.51, 100.0), (0.52, 100.0), (0.53, 100.0)],
        )
        result = self.filter.check(self.signal, ob)
        assert result.passed is True

    def test_no_depth_data_passes(self):
        """No ask_levels data -> fall back to basic check (passes)."""
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.51,
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(self.signal, ob)
        assert result.passed is True

    # --- Price movement / staleness filter tests ---

    def test_price_moved_too_much(self):
        """Price moved significantly for an old event -> rejected."""
        # Create an event from 60 seconds ago
        old_event = make_event(block_timestamp=int(time.time()) - 60)
        signal = TradeSignal(
            event=old_event,
            action=SignalAction.COPY_BUY,
            whale_address=WHALE_ADDRESS,
            whale_price=0.50,
        )
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.50,
            best_ask=0.52,  # 4% move — for a 60s old event, tolerance tightens
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(signal, ob)
        assert result.passed is False
        assert "Price moved" in result.reason

    def test_recent_event_small_movement_passes(self):
        """Recent event with small price movement -> passes."""
        recent_event = make_event(block_timestamp=int(time.time()) - 2)
        signal = TradeSignal(
            event=recent_event,
            action=SignalAction.COPY_BUY,
            whale_address=WHALE_ADDRESS,
            whale_price=0.50,
        )
        ob = OrderBookSnapshot(
            token_id="123",
            best_bid=0.49,
            best_ask=0.505,  # 1% move — should be fine for recent event
            bid_size=100.0,
            ask_size=100.0,
        )
        result = self.filter.check(signal, ob)
        assert result.passed is True
