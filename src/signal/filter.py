"""Trade filter: pre-execution risk checks and liquidity validation."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config import AppConfig
from src.models.market import MarketInfo, OrderBookSnapshot
from src.models.signals import TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class FilterResult:
    passed: bool
    reason: str = ""


class TradeFilter:
    """Applies risk and liquidity checks before trade execution."""

    def __init__(self, config: AppConfig) -> None:
        self._max_slippage_pct = config.trading.max_slippage_pct
        self._copy_amount_usd = config.trading.copy_amount_usd
        self._max_copy_amount_usd = config.trading.max_copy_amount_usd
        self._min_hours_to_resolution = config.trading.min_hours_to_resolution
        self._min_depth_multiplier = config.trading.min_depth_multiplier
        self._max_price_movement_pct = config.trading.max_price_movement_pct

    def check(
        self,
        signal: TradeSignal,
        orderbook: OrderBookSnapshot | None,
        market_info: MarketInfo | None = None,
    ) -> FilterResult:
        """Run all pre-trade checks."""
        # Check orderbook availability
        if orderbook is None:
            return FilterResult(False, "No orderbook data available")

        # Check that there's a valid ask
        if orderbook.best_ask is None or orderbook.best_ask <= 0:
            return FilterResult(False, "No valid ask price in orderbook")

        # Check ask size (need at least some liquidity)
        if orderbook.ask_size <= 0:
            return FilterResult(False, "No ask liquidity available")

        # Check price is reasonable (not above 0.99 - nearly resolved market)
        if orderbook.best_ask >= 0.99:
            return FilterResult(False, f"Price too high ({orderbook.best_ask:.3f}), market likely resolved")

        # Check slippage: compare best ask to whale's price
        if signal.whale_price > 0 and orderbook.best_ask > 0:
            slippage_pct = (
                (orderbook.best_ask - signal.whale_price) / signal.whale_price
            ) * 100
            if slippage_pct > self._max_slippage_pct:
                return FilterResult(
                    False,
                    f"Slippage too high: {slippage_pct:.1f}% > {self._max_slippage_pct}%",
                )

        # Time-to-resolution check
        if market_info and market_info.end_date:
            resolution_result = self._check_resolution_time(market_info)
            if not resolution_result.passed:
                return resolution_result

        # Orderbook depth check
        depth_result = self._check_depth(orderbook)
        if not depth_result.passed:
            return depth_result

        # Price movement / staleness check
        if signal.whale_price > 0 and orderbook.best_ask > 0:
            movement_result = self._check_price_movement(signal, orderbook)
            if not movement_result.passed:
                return movement_result

        return FilterResult(True)

    def _check_resolution_time(self, market_info: MarketInfo) -> FilterResult:
        """Reject markets about to resolve."""
        try:
            end_dt = datetime.fromisoformat(market_info.end_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_remaining = (end_dt - now).total_seconds() / 3600
            if hours_remaining < self._min_hours_to_resolution:
                return FilterResult(
                    False,
                    f"Market expires in {hours_remaining:.1f}h < {self._min_hours_to_resolution}h minimum",
                )
        except (ValueError, TypeError):
            # Can't parse end_date — allow the trade
            pass
        return FilterResult(True)

    def _check_depth(self, orderbook: OrderBookSnapshot) -> FilterResult:
        """Require sufficient orderbook depth around best ask."""
        if not orderbook.ask_levels:
            # No depth data available — fall back to basic ask_size check (already passed above)
            return FilterResult(True)

        available = orderbook.liquidity_within_pct(self._max_slippage_pct)
        required = self._max_copy_amount_usd * self._min_depth_multiplier
        if available < required:
            return FilterResult(
                False,
                f"Insufficient depth: ${available:.2f} < ${required:.2f} required",
            )
        return FilterResult(True)

    def _check_price_movement(
        self, signal: TradeSignal, orderbook: OrderBookSnapshot
    ) -> FilterResult:
        """Check if price has moved significantly since the whale traded.

        Tighter tolerance for older events: halve allowed movement per 30s of age,
        capped at 4x tightening.
        """
        base_max = self._max_price_movement_pct
        event_age = time.time() - signal.event.block_timestamp
        if event_age > 0:
            # Each 30s of age halves the allowed movement, cap at 4x tightening
            age_factor = max(1, min(4, 1 + event_age / 30))
            adjusted_max = base_max / age_factor
        else:
            adjusted_max = base_max

        movement_pct = abs(
            (orderbook.best_ask - signal.whale_price) / signal.whale_price
        ) * 100
        if movement_pct > adjusted_max:
            return FilterResult(
                False,
                f"Price moved {movement_pct:.1f}% (max {adjusted_max:.1f}% for {event_age:.0f}s old event)",
            )
        return FilterResult(True)
