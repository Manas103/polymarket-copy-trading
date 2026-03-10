"""Trading pipeline: orchestrates the full copy trading flow."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import AppConfig
from src.market.resolver import MarketResolver
from src.models.signals import SignalAction
from src.monitor.blockchain import BlockchainMonitor
from src.monitor.event_parser import EventParser
from src.persistence.repository import Repository
from src.risk.manager import RiskManager
from src.signal.filter import TradeFilter
from src.signal.generator import SignalGenerator
from src.signal.whale_activity_tracker import WhaleActivityTracker
from src.signal.whale_profiler import WhaleProfiler
from src.executor.trade_executor import TradeExecutor

if TYPE_CHECKING:
    from src.notifier.telegram import TelegramNotifier
    from src.signal.confluence import ConfluenceDetector

logger = logging.getLogger(__name__)


class TradingPipeline:
    """Orchestrates: Monitor -> Parse -> Signal -> Filter -> Execute -> Persist."""

    def __init__(
        self,
        config: AppConfig,
        monitor: BlockchainMonitor,
        parser: EventParser,
        signal_gen: SignalGenerator,
        trade_filter: TradeFilter,
        market_resolver: MarketResolver,
        risk_manager: RiskManager,
        executor: TradeExecutor,
        repository: Repository,
        whale_profiler: WhaleProfiler,
        activity_tracker: WhaleActivityTracker,
        confluence: "ConfluenceDetector | None" = None,
        notifier: "TelegramNotifier | None" = None,
    ) -> None:
        self._config = config
        self._monitor = monitor
        self._parser = parser
        self._signal_gen = signal_gen
        self._filter = trade_filter
        self._resolver = market_resolver
        self._risk = risk_manager
        self._executor = executor
        self._repo = repository
        self._profiler = whale_profiler
        self._activity = activity_tracker
        self._confluence = confluence
        self._notifier = notifier

    async def initialize(self) -> None:
        """Load last block from DB and restore dedup state."""
        last_block = await self._repo.get_last_block()
        if last_block is not None:
            self._monitor.set_last_block(last_block)
            logger.info("Resuming from block %d", last_block)

            # Restore dedup keys from DB so restart doesn't replay events
            dedup_keys = await self._repo.get_recent_dedup_keys(last_block)
            for key in dedup_keys:
                self._parser.mark_seen(key)
            logger.info("Loaded %d recent dedup keys", len(dedup_keys))
        else:
            logger.info("No saved block cursor, starting from latest")

    async def run(self) -> None:
        """Main loop: poll blocks and process events."""
        await self.initialize()

        async for logs, block_timestamps in self._monitor.poll_events():
            if not logs:
                # Save cursor even when no events
                if block_timestamps or True:
                    await self._repo.set_last_block(self._monitor._last_block)  # type: ignore[arg-type]
                continue

            # Parse logs into events
            events = self._parser.parse_logs(logs, block_timestamps)
            if not events:
                await self._repo.set_last_block(self._monitor._last_block)  # type: ignore[arg-type]
                continue

            logger.info("Parsed %d events from %d logs", len(events), len(logs))

            # Generate signals
            signals = await self._signal_gen.process_events(events)

            for signal in signals:
                # Persist the event (second defense against duplicate processing)
                is_new = await self._repo.insert_event(signal.event)
                if not is_new:
                    signal.action = SignalAction.SKIP_DUPLICATE
                    await self._repo.insert_signal(signal)
                    logger.debug(
                        "Duplicate event skipped: %s", signal.event.dedup_key
                    )
                    continue

                if not signal.should_copy:
                    # Persist skipped signal
                    await self._repo.insert_signal(signal)
                    logger.debug(
                        "Signal skipped: %s for %s",
                        signal.action.value,
                        signal.event.dedup_key,
                    )
                    continue

                # --- SELL PATH ---
                if signal.is_sell:
                    token_id = str(signal.event.token_id)

                    # Resolve market metadata
                    market_info = await self._resolver.resolve_market(token_id)
                    if market_info and market_info.condition_id:
                        signal.market_question = market_info.question
                        signal.outcome = market_info.outcome
                        signal.condition_id = market_info.condition_id
                        signal.neg_risk = market_info.neg_risk

                    # Get orderbook for sell pricing
                    orderbook = await self._resolver.get_orderbook(token_id)
                    if orderbook:
                        signal.current_best_ask = orderbook.best_ask
                        signal.current_midpoint = orderbook.midpoint

                    # Risk check (cooldown only, amount_usd=0)
                    risk_check = await self._risk.check_trade(
                        0.0, token_id, signal.condition_id
                    )
                    if not risk_check.allowed:
                        signal_id = await self._repo.insert_signal(signal)
                        logger.info(
                            "Sell blocked by risk: %s for token %s",
                            risk_check.reason,
                            token_id,
                        )
                        continue

                    # Get our position
                    invested_usd, position_tokens = await self._repo.get_position(token_id)
                    if position_tokens <= 0:
                        signal.action = SignalAction.SKIP_SELL
                        await self._repo.insert_signal(signal)
                        continue

                    # Persist signal and build sell trade
                    signal_id = await self._repo.insert_signal(signal)
                    trade = self._executor.build_copy_trade(
                        signal, orderbook, signal_id, position_tokens=position_tokens  # type: ignore[arg-type]
                    )
                    trade_id = await self._repo.insert_trade(trade)

                    logger.info(
                        "Executing copy SELL: %.2f tokens of token %s (signal: %s)",
                        position_tokens,
                        token_id,
                        signal.whale_address[:10],
                    )

                    result = await self._executor.execute(trade)
                    await self._repo.update_trade_result(trade_id, result)
                    if self._notifier:
                        await self._notifier.notify_trade(signal, trade, result)

                    if result.status.value in ("FILLED", "PARTIALLY_FILLED"):
                        self._risk.record_trade()
                        await self._repo.close_position(token_id)
                        logger.info(
                            "Sell %s: %s (order: %s)",
                            result.status.value,
                            token_id,
                            result.order_id,
                        )
                    else:
                        logger.warning(
                            "Sell %s for token %s: %s",
                            result.status.value,
                            token_id,
                            result.error_message,
                        )
                    continue

                # --- BUY PATH ---
                # Conviction check
                conviction = await self._profiler.check_conviction(
                    signal.whale_address, signal.event.usdc_amount
                )
                if not conviction.passed:
                    signal.action = SignalAction.SKIP_LOW_CONVICTION
                    await self._repo.insert_signal(signal)
                    logger.info(
                        "Low conviction: %s for %s",
                        conviction.reason,
                        signal.event.dedup_key,
                    )
                    continue

                # Store conviction_pct for dynamic sizing
                signal.conviction_pct = conviction.conviction_pct

                # Whale activity check
                token_id = str(signal.event.token_id)
                activity = await self._activity.check_activity(
                    signal.whale_address, token_id
                )
                if not activity.passed:
                    signal.action = SignalAction.SKIP_WHALE_EXITING
                    await self._repo.insert_signal(signal)
                    logger.info(
                        "Whale exiting: %s for token %s",
                        activity.reason,
                        token_id,
                    )
                    continue

                # Resolve market metadata
                market_info = await self._resolver.resolve_market(token_id)
                if not market_info or not market_info.condition_id:
                    signal.action = SignalAction.SKIP_MARKET_RESOLUTION_FAILED
                    await self._repo.insert_signal(signal)
                    logger.warning(
                        "Market resolution failed for token %s", token_id
                    )
                    continue

                signal.market_question = market_info.question
                signal.outcome = market_info.outcome
                signal.condition_id = market_info.condition_id
                signal.neg_risk = market_info.neg_risk

                # Get orderbook
                orderbook = await self._resolver.get_orderbook(token_id)
                if orderbook:
                    signal.current_best_ask = orderbook.best_ask
                    signal.current_midpoint = orderbook.midpoint

                # Pre-trade filter (includes resolution time, depth, price movement)
                filter_result = self._filter.check(signal, orderbook, market_info)
                if not filter_result.passed:
                    # Determine specific skip action from filter reason
                    signal.action = self._filter_reason_to_action(
                        filter_result.reason
                    )
                    signal_id = await self._repo.insert_signal(signal)
                    logger.info(
                        "Trade filtered (%s): %s for token %s",
                        signal.action.value,
                        filter_result.reason,
                        token_id,
                    )
                    continue

                # Calculate dynamic copy amount
                copy_amount = self._executor.calculate_copy_amount(signal)

                # Risk check
                risk_check = await self._risk.check_trade(
                    copy_amount,
                    token_id,
                    signal.condition_id,
                )
                if not risk_check.allowed:
                    signal_id = await self._repo.insert_signal(signal)
                    logger.info(
                        "Trade blocked by risk: %s for token %s",
                        risk_check.reason,
                        token_id,
                    )
                    continue

                # Confluence detection
                final_amount = copy_amount
                if self._confluence is not None:
                    self._confluence.record_buy(token_id, signal.whale_address)
                    confluence = self._confluence.check_confluence(token_id)
                    final_amount = min(
                        copy_amount * confluence.multiplier,
                        self._config.trading.max_copy_amount_usd,
                    )

                # Persist signal and build trade
                signal_id = await self._repo.insert_signal(signal)

                trade = self._executor.build_copy_trade(
                    signal, orderbook, signal_id, override_amount=final_amount  # type: ignore[arg-type]
                )
                trade_id = await self._repo.insert_trade(trade)

                # Execute
                logger.info(
                    "Executing copy trade: $%.2f BUY of token %s (signal: %s, market: %s)",
                    trade.amount_usd,
                    token_id,
                    signal.whale_address[:10],
                    signal.market_question[:50] if signal.market_question else "unknown",
                )

                result = await self._executor.execute(trade)
                await self._repo.update_trade_result(trade_id, result)
                if self._notifier:
                    await self._notifier.notify_trade(signal, trade, result)

                if result.status.value in ("FILLED", "PARTIALLY_FILLED"):
                    self._risk.record_trade()
                    await self._repo.record_daily_trade(trade.amount_usd)
                    await self._repo.upsert_position(
                        token_id,
                        signal.condition_id,
                        trade.amount_usd,
                        result.filled_amount,
                    )
                    logger.info(
                        "Trade %s: %s (order: %s, filled: $%.2f @ %.4f)",
                        result.status.value,
                        token_id,
                        result.order_id,
                        result.filled_amount,
                        result.filled_price,
                    )
                else:
                    logger.warning(
                        "Trade %s for token %s: %s",
                        result.status.value,
                        token_id,
                        result.error_message,
                    )

            # Save block cursor after processing all events
            await self._repo.set_last_block(self._monitor._last_block)  # type: ignore[arg-type]

    @staticmethod
    def _filter_reason_to_action(reason: str) -> SignalAction:
        """Map filter rejection reason to a specific SignalAction."""
        reason_lower = reason.lower()
        if "expires" in reason_lower or "resolution" in reason_lower:
            return SignalAction.SKIP_MARKET_EXPIRING
        if "depth" in reason_lower:
            return SignalAction.SKIP_LOW_LIQUIDITY
        if "price moved" in reason_lower:
            return SignalAction.SKIP_PRICE_MOVED
        if "liquidity" in reason_lower:
            return SignalAction.SKIP_LOW_LIQUIDITY
        # Default for other filter rejections (slippage, price too high, etc.)
        return SignalAction.SKIP_BELOW_THRESHOLD
