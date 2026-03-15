"""Repository for all database CRUD operations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import aiosqlite

from src.models.events import ExchangeType, OrderFilledEvent
from src.models.signals import SignalAction, TradeSignal
from src.models.trades import CopyTrade, TradeResult, TradeStatus
from src.persistence.database import Database


class Repository:
    """CRUD operations for all tables."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def conn(self) -> aiosqlite.Connection:
        return self._db.conn

    # -- Block cursor --

    async def get_last_block(self) -> Optional[int]:
        cursor = await self.conn.execute(
            "SELECT last_block FROM block_cursor WHERE id = 1"
        )
        row = await cursor.fetchone()
        return row["last_block"] if row else None

    async def set_last_block(self, block_number: int) -> None:
        await self.conn.execute(
            """INSERT INTO block_cursor (id, last_block, updated_at)
               VALUES (1, ?, datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                 last_block = excluded.last_block,
                 updated_at = datetime('now')""",
            (block_number,),
        )
        await self.conn.commit()

    # -- Whale events --

    async def insert_event(self, event: OrderFilledEvent) -> bool:
        """Insert event, returns True if inserted (not duplicate)."""
        try:
            await self.conn.execute(
                """INSERT INTO whale_events
                   (dedup_key, tx_hash, log_index, block_number, block_timestamp,
                    exchange_type, exchange_address, order_hash,
                    maker, taker, maker_asset_id, taker_asset_id,
                    maker_amount_filled, taker_amount_filled, fee)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.dedup_key,
                    event.tx_hash,
                    event.log_index,
                    event.block_number,
                    event.block_timestamp,
                    event.exchange_type.value,
                    event.exchange_address,
                    event.order_hash,
                    event.maker,
                    event.taker,
                    str(event.maker_asset_id),
                    str(event.taker_asset_id),
                    str(event.maker_amount_filled),
                    str(event.taker_amount_filled),
                    str(event.fee),
                ),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def event_exists(self, dedup_key: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM whale_events WHERE dedup_key = ?", (dedup_key,)
        )
        return await cursor.fetchone() is not None

    # -- Trade signals --

    async def insert_signal(self, signal: TradeSignal) -> int:
        """Insert a trade signal, returns the signal id."""
        cursor = await self.conn.execute(
            """INSERT INTO trade_signals
               (event_dedup_key, action, whale_address,
                market_question, outcome, condition_id, neg_risk,
                whale_price, current_best_ask, current_midpoint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.event.dedup_key,
                signal.action.value,
                signal.whale_address,
                signal.market_question,
                signal.outcome,
                signal.condition_id,
                int(signal.neg_risk),
                signal.whale_price,
                signal.current_best_ask,
                signal.current_midpoint,
            ),
        )
        await self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    # -- Copy trades --

    async def insert_trade(self, trade: CopyTrade) -> int:
        """Insert a copy trade record, returns the trade id."""
        cursor = await self.conn.execute(
            """INSERT INTO copy_trades
               (signal_id, token_id, amount_usd, side, order_type,
                worst_price, neg_risk, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.signal_id,
                trade.token_id,
                trade.amount_usd,
                trade.side,
                trade.order_type,
                trade.worst_price,
                int(trade.neg_risk),
                TradeStatus.PENDING.value,
            ),
        )
        await self.conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def update_trade_result(self, trade_id: int, result: TradeResult) -> None:
        await self.conn.execute(
            """UPDATE copy_trades SET
                 status = ?, order_id = ?,
                 filled_amount = ?, filled_price = ?,
                 error_message = ?
               WHERE id = ?""",
            (
                result.status.value,
                result.order_id,
                result.filled_amount,
                result.filled_price,
                result.error_message,
                trade_id,
            ),
        )
        await self.conn.commit()

    # -- Daily risk --

    async def get_daily_risk(self, d: date | None = None) -> tuple[int, float]:
        """Returns (trade_count, total_spend_usd) for the given date."""
        d = d or date.today()
        cursor = await self.conn.execute(
            "SELECT trade_count, total_spend_usd FROM daily_risk WHERE date = ?",
            (d.isoformat(),),
        )
        row = await cursor.fetchone()
        if row:
            return row["trade_count"], row["total_spend_usd"]
        return 0, 0.0

    async def record_daily_trade(
        self, amount_usd: float, d: date | None = None
    ) -> None:
        d = d or date.today()
        await self.conn.execute(
            """INSERT INTO daily_risk (date, trade_count, total_spend_usd)
               VALUES (?, 1, ?)
               ON CONFLICT(date) DO UPDATE SET
                 trade_count = trade_count + 1,
                 total_spend_usd = total_spend_usd + excluded.total_spend_usd""",
            (d.isoformat(), amount_usd),
        )
        await self.conn.commit()

    # -- Positions --

    async def get_position(self, token_id: str) -> tuple[float, float]:
        """Returns (total_invested_usd, total_tokens) for a token."""
        cursor = await self.conn.execute(
            "SELECT total_invested_usd, total_tokens FROM positions WHERE token_id = ?",
            (token_id,),
        )
        row = await cursor.fetchone()
        if row:
            return row["total_invested_usd"], row["total_tokens"]
        return 0.0, 0.0

    async def get_position_by_condition(self, condition_id: str) -> float:
        """Returns total invested USD across all tokens for a condition."""
        cursor = await self.conn.execute(
            "SELECT COALESCE(SUM(total_invested_usd), 0.0) as total "
            "FROM positions WHERE condition_id = ?",
            (condition_id,),
        )
        row = await cursor.fetchone()
        return row["total"]  # type: ignore[index]

    async def upsert_position(
        self,
        token_id: str,
        condition_id: str,
        invested_usd: float,
        tokens: float,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO positions (token_id, condition_id, total_invested_usd, total_tokens, last_updated)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(token_id) DO UPDATE SET
                 total_invested_usd = total_invested_usd + excluded.total_invested_usd,
                 total_tokens = total_tokens + excluded.total_tokens,
                 last_updated = datetime('now')""",
            (token_id, condition_id, invested_usd, tokens),
        )
        await self.conn.commit()

    async def reduce_position(
        self, token_id: str, sold_usd: float, sold_tokens: float
    ) -> None:
        """Reduce a position by the sold amounts, flooring at zero."""
        await self.conn.execute(
            """UPDATE positions SET
                 total_invested_usd = MAX(0, total_invested_usd - ?),
                 total_tokens = MAX(0, total_tokens - ?),
                 last_updated = datetime('now')
               WHERE token_id = ?""",
            (sold_usd, sold_tokens, token_id),
        )
        await self.conn.commit()

    async def close_position(self, token_id: str) -> None:
        """Close a position by zeroing out invested and tokens."""
        await self.conn.execute(
            """UPDATE positions SET
                 total_invested_usd = 0,
                 total_tokens = 0,
                 last_updated = datetime('now')
               WHERE token_id = ?""",
            (token_id,),
        )
        await self.conn.commit()

    async def count_open_positions(self) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) as cnt FROM positions WHERE total_invested_usd > 0"
        )
        row = await cursor.fetchone()
        return row["cnt"]  # type: ignore[index]

    # -- Whale analytics --

    async def get_recent_dedup_keys(
        self, last_block: int, safety_margin: int = 200
    ) -> list[str]:
        """Load dedup_keys for events near the last processed block."""
        min_block = max(0, last_block - safety_margin)
        cursor = await self.conn.execute(
            "SELECT dedup_key FROM whale_events WHERE block_number >= ?",
            (min_block,),
        )
        rows = await cursor.fetchall()
        return [row["dedup_key"] for row in rows]

    async def get_whale_token_activity(
        self, whale_address: str, token_id: str, since_timestamp: int
    ) -> tuple[float, float]:
        """Get (buy_usd, sell_usd) for a whale on a specific token since timestamp.

        Buy = whale is buying tokens (maker_asset_id='0' when whale is maker,
        or taker_asset_id='0' when whale is taker, but need to check which side
        the token is on). We check both maker and taker roles.
        """
        addr = whale_address.lower()
        token_str = str(token_id)
        buy_usd = 0.0
        sell_usd = 0.0

        # Whale as maker
        cursor = await self.conn.execute(
            """SELECT maker_asset_id, taker_asset_id,
                      CAST(maker_amount_filled AS REAL) / 1e6 as maker_usd,
                      CAST(taker_amount_filled AS REAL) / 1e6 as taker_usd
            FROM whale_events
            WHERE LOWER(maker) = ? AND block_timestamp >= ?
              AND (maker_asset_id = ? OR taker_asset_id = ?)""",
            (addr, since_timestamp, token_str, token_str),
        )
        for row in await cursor.fetchall():
            if row["maker_asset_id"] == "0":
                # Maker pays USDC -> buying token (taker_asset_id is the token)
                if row["taker_asset_id"] == token_str:
                    buy_usd += row["maker_usd"]
            else:
                # Maker pays tokens -> selling token (maker_asset_id is the token)
                if row["maker_asset_id"] == token_str:
                    sell_usd += row["taker_usd"]

        # Whale as taker
        cursor = await self.conn.execute(
            """SELECT maker_asset_id, taker_asset_id,
                      CAST(maker_amount_filled AS REAL) / 1e6 as maker_usd,
                      CAST(taker_amount_filled AS REAL) / 1e6 as taker_usd
            FROM whale_events
            WHERE LOWER(taker) = ? AND block_timestamp >= ?
              AND (maker_asset_id = ? OR taker_asset_id = ?)""",
            (addr, since_timestamp, token_str, token_str),
        )
        for row in await cursor.fetchall():
            if row["taker_asset_id"] == "0":
                # Taker pays USDC -> buying token (maker_asset_id is the token)
                if row["maker_asset_id"] == token_str:
                    buy_usd += row["taker_usd"]
            else:
                # Taker pays tokens -> selling token (taker_asset_id is the token)
                if row["taker_asset_id"] == token_str:
                    sell_usd += row["maker_usd"]

        return buy_usd, sell_usd

    async def get_block_cursor_updated_at(self) -> str | None:
        """Return the updated_at timestamp from the block cursor."""
        cursor = await self.conn.execute(
            "SELECT updated_at FROM block_cursor WHERE id = 1"
        )
        row = await cursor.fetchone()
        return row["updated_at"] if row else None

    # -- Dashboard queries --

    async def get_recent_whale_signals(self, limit: int = 50) -> list[dict]:
        """Return recent whale signals with USD estimates."""
        cursor = await self.conn.execute(
            """SELECT ts.whale_address, ts.action, ts.market_question,
                      ts.outcome, ts.whale_price, ts.created_at,
                      we.maker_amount_filled, we.taker_amount_filled,
                      we.maker_asset_id
               FROM trade_signals ts
               LEFT JOIN whale_events we ON ts.event_dedup_key = we.dedup_key
               WHERE ts.whale_address != ''
               ORDER BY ts.id DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            maker_filled = float(r["maker_amount_filled"] or 0)
            taker_filled = float(r["taker_amount_filled"] or 0)
            maker_asset = str(r["maker_asset_id"] or "")
            usd = maker_filled / 1e6 if maker_asset == "0" else taker_filled / 1e6
            result.append({
                "whale_address": r["whale_address"],
                "action": r["action"],
                "market_question": r["market_question"] or "",
                "outcome": r["outcome"] or "",
                "usd_amount": usd,
                "created_at": r["created_at"],
            })
        return result

    async def get_all_open_positions(self) -> list[dict]:
        """Return all positions with invested > 0."""
        cursor = await self.conn.execute(
            """SELECT token_id, condition_id, total_invested_usd,
                      total_tokens, last_updated
               FROM positions WHERE total_invested_usd > 0
               ORDER BY last_updated DESC"""
        )
        rows = await cursor.fetchall()
        return [
            {
                "token_id": r["token_id"],
                "condition_id": r["condition_id"],
                "total_invested_usd": r["total_invested_usd"],
                "total_tokens": r["total_tokens"],
                "last_updated": r["last_updated"],
            }
            for r in rows
        ]

    async def get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Return the most recent copy trades joined with signal info."""
        cursor = await self.conn.execute(
            """SELECT ct.amount_usd, ct.side, ct.status,
                      ct.filled_amount, ct.filled_price, ct.created_at,
                      ts.market_question, ts.outcome, ts.whale_address
               FROM copy_trades ct
               JOIN trade_signals ts ON ct.signal_id = ts.id
               ORDER BY ct.id DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "amount_usd": r["amount_usd"],
                "side": r["side"],
                "status": r["status"],
                "filled_amount": r["filled_amount"],
                "filled_price": r["filled_price"],
                "created_at": r["created_at"],
                "market_question": r["market_question"],
                "outcome": r["outcome"],
                "whale_address": r["whale_address"],
            }
            for r in rows
        ]

    async def get_signal_action_counts(self, since_hours: int = 24) -> dict[str, int]:
        """Count signals grouped by action within the last N hours."""
        cursor = await self.conn.execute(
            """SELECT action, COUNT(*) as cnt
               FROM trade_signals
               WHERE created_at >= datetime('now', ? || ' hours')
               GROUP BY action""",
            (f"-{since_hours}",),
        )
        rows = await cursor.fetchall()
        return {r["action"]: r["cnt"] for r in rows}

    async def get_daily_spend_history(self, days: int = 7) -> list[dict]:
        """Return daily risk rows for the last N days."""
        cursor = await self.conn.execute(
            """SELECT date, trade_count, total_spend_usd
               FROM daily_risk
               WHERE date >= date('now', ? || ' days')
               ORDER BY date ASC""",
            (f"-{days - 1}",),
        )
        rows = await cursor.fetchall()
        return [
            {
                "date": r["date"],
                "trade_count": r["trade_count"],
                "total_spend_usd": r["total_spend_usd"],
            }
            for r in rows
        ]

    # -- Fill accumulator persistence --

    async def insert_fill(
        self, whale_address: str, token_id: str, usd_amount: float
    ) -> None:
        """Persist a fill for the accumulator."""
        await self.conn.execute(
            """INSERT INTO fill_accumulator (whale_address, token_id, usd_amount)
               VALUES (?, ?, ?)""",
            (whale_address.lower(), token_id, usd_amount),
        )
        await self.conn.commit()

    async def mark_accumulator_fired(
        self, whale_address: str, token_id: str
    ) -> None:
        """Record that a signal fired for this (whale, token) pair."""
        await self.conn.execute(
            """INSERT INTO fill_accumulator_fired (whale_address, token_id, fired_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(whale_address, token_id) DO UPDATE SET
                 fired_at = datetime('now')""",
            (whale_address.lower(), token_id),
        )
        await self.conn.commit()

    async def load_accumulator_fills(
        self, window_seconds: int
    ) -> list[tuple[str, str, float, str]]:
        """Load all fills within the window for startup restore.

        Returns list of (whale_address, token_id, usd_amount, recorded_at).
        """
        cursor = await self.conn.execute(
            """SELECT whale_address, token_id, usd_amount, recorded_at
               FROM fill_accumulator
               WHERE recorded_at >= datetime('now', ? || ' seconds')
               ORDER BY recorded_at ASC""",
            (f"-{window_seconds}",),
        )
        return [
            (r["whale_address"], r["token_id"], r["usd_amount"], r["recorded_at"])
            for r in await cursor.fetchall()
        ]

    async def load_accumulator_fired(
        self, cooldown_seconds: int
    ) -> list[tuple[str, str, str]]:
        """Load active fired records within cooldown.

        Returns list of (whale_address, token_id, fired_at).
        """
        cursor = await self.conn.execute(
            """SELECT whale_address, token_id, fired_at
               FROM fill_accumulator_fired
               WHERE fired_at >= datetime('now', ? || ' seconds')""",
            (f"-{cooldown_seconds}",),
        )
        return [
            (r["whale_address"], r["token_id"], r["fired_at"])
            for r in await cursor.fetchall()
        ]

    async def cleanup_old_fills(self, window_seconds: int) -> None:
        """Delete expired fill accumulator rows."""
        await self.conn.execute(
            "DELETE FROM fill_accumulator WHERE recorded_at < datetime('now', ? || ' seconds')",
            (f"-{window_seconds}",),
        )
        await self.conn.commit()

    # -- Housekeeping --

    async def cleanup_old_events(self, keep_days: int = 3) -> int:
        """Delete whale_events and trade_signals older than keep_days.

        Returns the number of whale_events deleted.
        """
        cutoff = f"-{keep_days} days"

        # Delete old trade_signals (references whale_events via dedup_key)
        await self.conn.execute(
            "DELETE FROM trade_signals WHERE created_at < datetime('now', ?)",
            (cutoff,),
        )

        cursor = await self.conn.execute(
            "DELETE FROM whale_events WHERE created_at < datetime('now', ?)",
            (cutoff,),
        )
        deleted = cursor.rowcount
        await self.conn.commit()
        return deleted
