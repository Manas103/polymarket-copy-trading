"""SQLite database connection and schema management."""

from __future__ import annotations

import aiosqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS block_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_block INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS whale_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL UNIQUE,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    block_timestamp INTEGER NOT NULL,
    exchange_type TEXT NOT NULL,
    exchange_address TEXT NOT NULL,
    order_hash TEXT NOT NULL,
    maker TEXT NOT NULL,
    taker TEXT NOT NULL,
    maker_asset_id TEXT NOT NULL,
    taker_asset_id TEXT NOT NULL,
    maker_amount_filled TEXT NOT NULL,
    taker_amount_filled TEXT NOT NULL,
    fee TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trade_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_dedup_key TEXT NOT NULL,
    action TEXT NOT NULL,
    whale_address TEXT NOT NULL DEFAULT '',
    market_question TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    condition_id TEXT NOT NULL DEFAULT '',
    neg_risk INTEGER NOT NULL DEFAULT 0,
    whale_price REAL NOT NULL DEFAULT 0.0,
    current_best_ask REAL,
    current_midpoint REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (event_dedup_key) REFERENCES whale_events(dedup_key)
);

CREATE TABLE IF NOT EXISTS copy_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    token_id TEXT NOT NULL,
    amount_usd REAL NOT NULL,
    side TEXT NOT NULL DEFAULT 'BUY',
    order_type TEXT NOT NULL DEFAULT 'FAK',
    worst_price REAL NOT NULL DEFAULT 0.0,
    neg_risk INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING',
    order_id TEXT NOT NULL DEFAULT '',
    filled_amount REAL NOT NULL DEFAULT 0.0,
    filled_price REAL NOT NULL DEFAULT 0.0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (signal_id) REFERENCES trade_signals(id)
);

CREATE TABLE IF NOT EXISTS daily_risk (
    date TEXT NOT NULL,
    trade_count INTEGER NOT NULL DEFAULT 0,
    total_spend_usd REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (date)
);

CREATE TABLE IF NOT EXISTS positions (
    token_id TEXT NOT NULL,
    condition_id TEXT NOT NULL DEFAULT '',
    total_invested_usd REAL NOT NULL DEFAULT 0.0,
    total_tokens REAL NOT NULL DEFAULT 0.0,
    last_updated TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (token_id)
);

CREATE INDEX IF NOT EXISTS idx_whale_events_maker_ts ON whale_events(LOWER(maker), block_timestamp);
CREATE INDEX IF NOT EXISTS idx_whale_events_taker_ts ON whale_events(LOWER(taker), block_timestamp);
CREATE INDEX IF NOT EXISTS idx_whale_events_block ON whale_events(block_number);
"""


class Database:
    """Async SQLite database connection manager."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn
