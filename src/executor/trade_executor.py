"""Trade executor: builds and submits copy trade orders."""

from __future__ import annotations

import logging

from py_clob_client.clob_types import MarketOrderArgs, OrderType

from config import AppConfig
from src.executor.clob_wrapper import AsyncClobWrapper
from src.models.market import OrderBookSnapshot
from src.models.signals import TradeSignal
from src.models.trades import CopyTrade, TradeResult, TradeStatus
from src.risk.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Builds and submits copy trade orders to the CLOB API."""

    def __init__(
        self,
        config: AppConfig,
        clob: AsyncClobWrapper,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        self._config = config
        self._clob = clob
        self._cb = circuit_breaker

    def calculate_copy_amount(self, signal: TradeSignal) -> float:
        """Calculate copy amount, optionally scaled by conviction."""
        cfg = self._config.trading
        if not cfg.conviction_scaling_enabled or signal.conviction_pct <= 0:
            return cfg.copy_amount_usd

        base = cfg.conviction_base_pct
        cap = cfg.conviction_max_pct
        if cap <= base:
            return cfg.copy_amount_usd

        t = max(0.0, min(1.0, (signal.conviction_pct - base) / (cap - base)))
        amount = cfg.copy_amount_usd + t * (cfg.max_copy_amount_usd - cfg.copy_amount_usd)
        return amount

    def build_copy_trade(
        self,
        signal: TradeSignal,
        orderbook: OrderBookSnapshot,
        signal_id: int,
        position_tokens: float = 0.0,
        override_amount: float | None = None,
    ) -> CopyTrade:
        """Build a CopyTrade from a signal and orderbook."""
        if signal.is_sell:
            worst_price = self._calculate_worst_sell_price(orderbook)
            return CopyTrade(
                token_id=str(signal.event.token_id),
                amount_usd=position_tokens,  # CLOB API takes token count for sells
                side="SELL",
                order_type=self._config.trading.order_type,
                worst_price=worst_price,
                neg_risk=signal.neg_risk,
                signal_id=signal_id,
            )

        worst_price = self._calculate_worst_price(orderbook)
        amount = override_amount if override_amount is not None else self.calculate_copy_amount(signal)

        return CopyTrade(
            token_id=str(signal.event.token_id),
            amount_usd=amount,
            side="BUY",
            order_type=self._config.trading.order_type,
            worst_price=worst_price,
            neg_risk=signal.neg_risk,
            signal_id=signal_id,
        )

    async def execute(self, trade: CopyTrade) -> TradeResult:
        """Submit a copy trade order."""
        if not self._cb.can_execute():
            return TradeResult(
                status=TradeStatus.REJECTED,
                error_message="Circuit breaker is open",
            )

        try:
            order_type = OrderType.FAK if trade.order_type == "FAK" else OrderType.FOK

            args = MarketOrderArgs(
                token_id=trade.token_id,
                amount=trade.amount_usd,
                side=trade.side,
                price=trade.worst_price,
                order_type=order_type,
            )

            result = await self._clob.create_and_post_market_order(
                args, order_type, neg_risk=trade.neg_risk
            )

            self._cb.record_success()

            return self._parse_result(result)

        except Exception as e:
            self._cb.record_failure()
            logger.exception("Trade execution failed for token %s", trade.token_id)
            return TradeResult(
                status=TradeStatus.ERROR,
                error_message=str(e),
            )

    def _calculate_worst_price(self, orderbook: OrderBookSnapshot) -> float:
        """Calculate worst acceptable buy price with slippage."""
        if orderbook.best_ask is None:
            return 0.99  # Fallback max price

        slippage = self._config.trading.max_slippage_pct / 100
        worst = orderbook.best_ask * (1 + slippage)
        return min(worst, 0.99)  # Cap at 0.99

    def _calculate_worst_sell_price(self, orderbook: OrderBookSnapshot) -> float:
        """Calculate worst acceptable sell price with slippage."""
        if orderbook.best_bid is None:
            return 0.01  # Fallback min price

        slippage = self._config.trading.max_slippage_pct / 100
        worst = orderbook.best_bid * (1 - slippage)
        return max(worst, 0.01)  # Floor at 0.01

    def _parse_result(self, result: dict) -> TradeResult:
        """Parse CLOB API response into TradeResult."""
        if not result:
            return TradeResult(
                status=TradeStatus.ERROR,
                error_message="Empty response from CLOB API",
            )

        status_str = result.get("status", "")
        order_id = result.get("orderID", result.get("order_id", ""))

        if status_str == "matched":
            return TradeResult(
                status=TradeStatus.FILLED,
                order_id=order_id,
                filled_amount=float(result.get("matchedAmount", 0)),
                filled_price=float(result.get("avgPrice", 0)),
            )
        elif status_str == "delayed":
            # Order accepted, will be matched
            return TradeResult(
                status=TradeStatus.FILLED,
                order_id=order_id,
            )
        else:
            return TradeResult(
                status=TradeStatus.REJECTED,
                order_id=order_id,
                error_message=f"Unexpected status: {status_str}",
            )
