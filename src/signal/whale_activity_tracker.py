"""Whale sell-to-buy ratio detection."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from config import AppConfig
from src.persistence.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class WhaleActivityResult:
    passed: bool
    buy_usd: float = 0.0
    sell_usd: float = 0.0
    ratio: float = 0.0
    reason: str = ""


class WhaleActivityTracker:
    """Detects when a whale is net-exiting a position despite individual buys."""

    def __init__(self, config: AppConfig, repository: Repository) -> None:
        self._max_ratio = config.trading.max_sell_to_buy_ratio
        self._window_hours = config.trading.activity_window_hours
        self._repo = repository

    async def check_activity(
        self, whale_address: str, token_id: str
    ) -> WhaleActivityResult:
        """Check if whale is net-exiting this token in the recent window.

        Passes if:
        - No sell activity
        - sell_volume / buy_volume <= max_sell_to_buy_ratio
        """
        since_ts = int(time.time() - self._window_hours * 3600)
        buy_usd, sell_usd = await self._repo.get_whale_token_activity(
            whale_address, token_id, since_ts
        )

        if sell_usd <= 0:
            return WhaleActivityResult(
                passed=True,
                buy_usd=buy_usd,
                sell_usd=sell_usd,
            )

        if buy_usd <= 0:
            # Only sells, no buys — whale is exiting
            reason = (
                f"Whale exiting: ${sell_usd:.0f} sold, $0 bought "
                f"for token {token_id} in last {self._window_hours}h"
            )
            logger.info(reason)
            return WhaleActivityResult(
                passed=False,
                buy_usd=buy_usd,
                sell_usd=sell_usd,
                reason=reason,
            )

        ratio = sell_usd / buy_usd
        if ratio > self._max_ratio:
            reason = (
                f"Whale net-exiting: sell/buy ratio {ratio:.2f} > {self._max_ratio} "
                f"(${sell_usd:.0f} sold vs ${buy_usd:.0f} bought)"
            )
            logger.info(reason)
            return WhaleActivityResult(
                passed=False,
                buy_usd=buy_usd,
                sell_usd=sell_usd,
                ratio=ratio,
                reason=reason,
            )

        return WhaleActivityResult(
            passed=True,
            buy_usd=buy_usd,
            sell_usd=sell_usd,
            ratio=ratio,
        )
