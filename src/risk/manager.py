"""Risk manager: enforces daily limits, position limits, and cooldowns."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from config import AppConfig
from src.persistence.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class RiskCheck:
    allowed: bool
    reason: str = ""


class RiskManager:
    """Enforces risk limits before trade execution."""

    def __init__(self, config: AppConfig, repository: Repository) -> None:
        self._config = config.risk
        self._repo = repository
        self._last_trade_time: float = 0

    async def check_trade(
        self, amount_usd: float, token_id: str, condition_id: str
    ) -> RiskCheck:
        """Check if a trade is allowed under current risk limits."""
        # Cooldown check
        elapsed = time.monotonic() - self._last_trade_time
        if self._last_trade_time > 0 and elapsed < self._config.cooldown_seconds:
            remaining = self._config.cooldown_seconds - elapsed
            return RiskCheck(False, f"Cooldown: {remaining:.0f}s remaining")

        # Daily trade count
        trade_count, daily_spend = await self._repo.get_daily_risk()
        if trade_count >= self._config.max_daily_trades:
            return RiskCheck(False, f"Daily trade limit reached: {trade_count}/{self._config.max_daily_trades}")

        # Daily spend
        if daily_spend + amount_usd > self._config.max_daily_spend_usd:
            return RiskCheck(
                False,
                f"Daily spend limit: ${daily_spend:.2f} + ${amount_usd:.2f} > ${self._config.max_daily_spend_usd:.2f}",
            )

        # Position limit per market (by condition_id)
        if condition_id:
            position_usd = await self._repo.get_position_by_condition(condition_id)
            if position_usd + amount_usd > self._config.max_position_per_market_usd:
                return RiskCheck(
                    False,
                    f"Position limit for market: ${position_usd:.2f} + ${amount_usd:.2f} > ${self._config.max_position_per_market_usd:.2f}",
                )

        # Open positions count
        open_positions = await self._repo.count_open_positions()
        if open_positions >= self._config.max_open_positions:
            # Only block if this is a new position
            existing, _ = await self._repo.get_position(token_id)
            if existing == 0:
                return RiskCheck(
                    False,
                    f"Max open positions reached: {open_positions}/{self._config.max_open_positions}",
                )

        return RiskCheck(True)

    def record_trade(self) -> None:
        """Record that a trade was executed (for cooldown tracking)."""
        self._last_trade_time = time.monotonic()
