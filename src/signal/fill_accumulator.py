"""Fill accumulator: aggregates whale fills per token over a time window."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config import AppConfig

if TYPE_CHECKING:
    from src.persistence.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class AccumulatedFill:
    aggregate_usd: float
    already_fired: bool
    fill_count: int = 0


class FillAccumulator:
    """Tracks cumulative USD per (whale, token) over a rolling time window.

    When a whale builds a position across many small fills, this aggregates
    them so the conviction filter sees the total amount, not each tiny fill.
    Once a signal fires, it marks (whale, token) as fired to prevent duplicates.

    State is persisted to SQLite when a repository is provided, and restored
    on startup via restore_from_db().
    """

    def __init__(self, config: AppConfig, repo: Repository | None = None) -> None:
        self._window = config.trading.fill_accumulator_window_seconds
        self._cooldown = config.trading.fill_accumulator_cooldown_seconds
        self._repo = repo
        # (whale_lower, token_id) -> [(usd_amount, wall_clock_time)]
        self._fills: dict[tuple[str, str], list[tuple[float, float]]] = {}
        # (whale_lower, token_id) -> wall_clock_time when signal fired
        self._fired: dict[tuple[str, str], float] = {}

    async def restore_from_db(self) -> None:
        """Reload accumulator state from SQLite after a restart."""
        if not self._repo:
            return

        # Load fills
        fills = await self._repo.load_accumulator_fills(self._window)
        for whale, token_id, usd, recorded_at in fills:
            key = (whale.lower(), token_id)
            if key not in self._fills:
                self._fills[key] = []
            ts = _parse_utc_timestamp(recorded_at)
            self._fills[key].append((usd, ts))

        # Load fired state
        fired = await self._repo.load_accumulator_fired(self._cooldown)
        for whale, token_id, fired_at in fired:
            key = (whale.lower(), token_id)
            self._fired[key] = _parse_utc_timestamp(fired_at)

        # Cleanup old rows
        await self._repo.cleanup_old_fills(self._window)

        total_fills = sum(len(v) for v in self._fills.values())
        if total_fills > 0 or self._fired:
            logger.info(
                "Restored accumulator: %d fills across %d pairs, %d fired",
                total_fills, len(self._fills), len(self._fired),
            )

    async def record_fill(self, whale_address: str, token_id: str, usd_amount: float) -> None:
        """Record a whale buy fill."""
        now = time.time()
        key = (whale_address.lower(), token_id)
        if key not in self._fills:
            self._fills[key] = []
        self._prune(key, now)
        self._fills[key].append((usd_amount, now))

        # Persist to DB
        if self._repo:
            await self._repo.insert_fill(whale_address, token_id, usd_amount)

        entries = self._fills[key]
        total = sum(usd for usd, _ in entries)
        remaining_secs = self._window - (now - entries[0][1]) if entries else 0
        logger.info(
            "Fill recorded: %s…%s on %s… — $%.0f this fill, $%.0f total (%d fills, %.0fs left in window)",
            whale_address[:6], whale_address[-4:],
            token_id[:12],
            usd_amount, total, len(entries), max(0, remaining_secs),
        )

    def get_aggregate(self, whale_address: str, token_id: str) -> AccumulatedFill:
        """Get cumulative USD and fired status for a (whale, token) pair."""
        now = time.time()
        key = (whale_address.lower(), token_id)
        self._prune(key, now)

        entries = self._fills.get(key, [])
        total = sum(usd for usd, _ in entries)

        fired = self._is_fired(key, now)

        return AccumulatedFill(aggregate_usd=total, already_fired=fired, fill_count=len(entries))

    async def mark_fired(self, whale_address: str, token_id: str) -> None:
        """Mark a (whale, token) pair as having fired a signal."""
        key = (whale_address.lower(), token_id)
        self._fired[key] = time.time()

        if self._repo:
            await self._repo.mark_accumulator_fired(whale_address, token_id)

    def get_active_accumulations(self) -> list[dict]:
        """Return unfired accumulations for dashboard display."""
        now = time.time()
        result = []
        for (whale, token_id), entries in list(self._fills.items()):
            # Prune expired fills
            cutoff = now - self._window
            active = [(usd, t) for usd, t in entries if t >= cutoff]
            if not active:
                continue
            if self._is_fired((whale, token_id), now):
                continue
            total_usd = sum(usd for usd, _ in active)
            if total_usd <= 0:
                continue
            first_t = min(t for _, t in active)
            last_t = max(t for _, t in active)
            result.append({
                "whale_address": whale,
                "token_id": token_id,
                "total_usd": total_usd,
                "fill_count": len(active),
                "first_fill": datetime.fromtimestamp(first_t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "last_fill": datetime.fromtimestamp(last_t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            })
        result.sort(key=lambda x: x["total_usd"], reverse=True)
        return result

    def _is_fired(self, key: tuple[str, str], now: float) -> bool:
        """Check if signal already fired within cooldown period."""
        fired_at = self._fired.get(key)
        if fired_at is None:
            return False
        if now - fired_at > self._cooldown:
            del self._fired[key]
            return False
        return True

    def _prune(self, key: tuple[str, str], now: float) -> None:
        """Remove fills outside the time window."""
        entries = self._fills.get(key)
        if not entries:
            return
        cutoff = now - self._window
        self._fills[key] = [(usd, t) for usd, t in entries if t >= cutoff]


def _parse_utc_timestamp(ts: str) -> float:
    """Convert a UTC datetime string from SQLite to a time.time() value."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return time.time()
