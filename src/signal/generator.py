"""Signal generator: detects whale buys from parsed events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional, Sequence

from config import AppConfig
from src.models.events import OrderFilledEvent
from src.models.signals import SignalAction, TradeSignal

if TYPE_CHECKING:
    from src.persistence.repository import Repository

logger = logging.getLogger(__name__)


class SignalGenerator:
    """Filters OrderFilledEvents to generate TradeSignals for whale buys."""

    def __init__(self, config: AppConfig, repository: Optional["Repository"] = None) -> None:
        self._whale_addresses = {
            addr.lower() for addr in config.whales.addresses
        }
        self._min_usd = config.trading.min_whale_trade_usd
        self._repo = repository

    async def process_events(
        self, events: Sequence[OrderFilledEvent]
    ) -> list[TradeSignal]:
        """Process events and generate trade signals."""
        signals: list[TradeSignal] = []
        for event in events:
            signal = await self._classify(event)
            signals.append(signal)
        return signals

    async def _classify(self, event: OrderFilledEvent) -> TradeSignal:
        """Classify a single event into a TradeSignal."""
        maker_lower = event.maker.lower()
        taker_lower = event.taker.lower()

        # Check if whale is involved as maker or taker
        whale_addr = ""
        is_whale = False
        if maker_lower in self._whale_addresses:
            whale_addr = event.maker
            is_whale = True
        elif taker_lower in self._whale_addresses:
            whale_addr = event.taker
            is_whale = True

        if not is_whale:
            return TradeSignal(
                event=event,
                action=SignalAction.SKIP_NOT_WHALE,
            )

        # Determine if whale is buying
        whale_is_buying = False
        if maker_lower == whale_addr.lower():
            # Whale is the maker
            whale_is_buying = event.is_maker_buying  # maker_asset_id == 0 means buying
        else:
            # Whale is the taker
            # Taker buys when taker_asset_id == 0 (taker pays USDC)
            whale_is_buying = event.taker_asset_id == 0

        if not whale_is_buying:
            # Whale is selling — check if we hold a position
            if self._repo is not None:
                token_id = str(event.token_id)
                invested, _ = await self._repo.get_position(token_id)
                if invested > 0:
                    return TradeSignal(
                        event=event,
                        action=SignalAction.COPY_SELL,
                        whale_address=whale_addr,
                        whale_price=self._estimate_price(event),
                    )
            return TradeSignal(
                event=event,
                action=SignalAction.SKIP_SELL,
                whale_address=whale_addr,
            )

        # Check minimum trade size
        if event.usdc_amount < self._min_usd:
            return TradeSignal(
                event=event,
                action=SignalAction.SKIP_BELOW_THRESHOLD,
                whale_address=whale_addr,
                whale_price=self._estimate_price(event),
            )

        logger.info(
            "Whale BUY detected: %s bought $%.2f of token %s in tx %s",
            whale_addr,
            event.usdc_amount,
            event.token_id,
            event.tx_hash,
        )

        return TradeSignal(
            event=event,
            action=SignalAction.COPY_BUY,
            whale_address=whale_addr,
            whale_price=self._estimate_price(event),
        )

    def _estimate_price(self, event: OrderFilledEvent) -> float:
        """Estimate the effective price from the event amounts."""
        if event.maker_asset_id == 0:
            # Maker buys: pays maker_amount (USDC), receives taker_amount (tokens)
            if event.taker_amount_filled > 0:
                return event.maker_amount_filled / event.taker_amount_filled
        else:
            # Taker buys: pays taker_amount (USDC), receives maker_amount (tokens)
            if event.maker_amount_filled > 0:
                return event.taker_amount_filled / event.maker_amount_filled
        return 0.0
