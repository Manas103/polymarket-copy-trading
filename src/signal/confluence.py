"""Multi-whale confluence detection."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import AppConfig


@dataclass
class ConfluenceResult:
    whale_count: int
    multiplier: float
    whale_addresses: list[str] = field(default_factory=list)


class ConfluenceDetector:
    """Detects when multiple whales buy the same token within a time window."""

    def __init__(self, config: AppConfig) -> None:
        self._enabled = config.trading.confluence_enabled
        self._window = config.trading.confluence_window_seconds
        self._min_whales = config.trading.confluence_min_whales
        self._multiplier = config.trading.confluence_multiplier
        self._max_multiplier = config.trading.confluence_max_multiplier
        # token_id -> [(whale_address, monotonic_time)]
        self._buys: dict[str, list[tuple[str, float]]] = {}

    def record_buy(self, token_id: str, whale_address: str) -> None:
        """Record a whale buy for a token."""
        now = time.monotonic()
        if token_id not in self._buys:
            self._buys[token_id] = []
        self._prune(token_id, now)
        self._buys[token_id].append((whale_address.lower(), now))

    def check_confluence(self, token_id: str) -> ConfluenceResult:
        """Check how many unique whales have bought this token recently."""
        if not self._enabled:
            return ConfluenceResult(whale_count=1, multiplier=1.0)

        now = time.monotonic()
        self._prune(token_id, now)

        entries = self._buys.get(token_id, [])
        unique_whales = list({addr for addr, _ in entries})
        count = len(unique_whales)

        if count < self._min_whales:
            return ConfluenceResult(
                whale_count=count, multiplier=1.0, whale_addresses=unique_whales
            )

        # 2 whales -> confluence_multiplier (2.0)
        # 3+ whales -> confluence_multiplier + 0.5 * (count - 2), capped
        multiplier = self._multiplier + 0.5 * (count - self._min_whales)
        multiplier = min(multiplier, self._max_multiplier)

        return ConfluenceResult(
            whale_count=count, multiplier=multiplier, whale_addresses=unique_whales
        )

    def _prune(self, token_id: str, now: float) -> None:
        """Remove entries outside the time window."""
        entries = self._buys.get(token_id)
        if not entries:
            return
        cutoff = now - self._window
        self._buys[token_id] = [(addr, t) for addr, t in entries if t >= cutoff]
