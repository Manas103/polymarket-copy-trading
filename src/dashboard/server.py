"""Dashboard web server for monitoring the copy trading bot."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import date

import aiosqlite
from aiohttp import web

from config import AppConfig
from src.dashboard.template import render_dashboard

logger = logging.getLogger(__name__)


class DashboardServer:
    """Lightweight aiohttp dashboard that runs alongside the trading pipeline."""

    def __init__(self, config: AppConfig, conn: aiosqlite.Connection) -> None:
        self._config = config
        self._conn = conn
        self._start_time = time.time()
        self.port = int(os.getenv("DASHBOARD_PORT", "8080"))

    async def _query(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        """Run a read query, returning empty list on transient errors."""
        try:
            cursor = await self._conn.execute(sql, params)
            return await cursor.fetchall()
        except Exception:
            logger.warning("Dashboard query failed (transient), returning empty", exc_info=True)
            return []

    async def _collect_data(self) -> dict:
        uptime_secs = time.time() - self._start_time

        # last block
        rows = await self._query("SELECT last_block FROM block_cursor WHERE id = 1")
        last_block = rows[0]["last_block"] if rows else None

        # daily risk
        today = date.today().isoformat()
        rows = await self._query(
            "SELECT trade_count, total_spend_usd FROM daily_risk WHERE date = ?",
            (today,),
        )
        trade_count = rows[0]["trade_count"] if rows else 0
        total_spend = rows[0]["total_spend_usd"] if rows else 0.0

        # open positions
        rows = await self._query(
            """SELECT token_id, condition_id, total_invested_usd,
                      total_tokens, last_updated
               FROM positions WHERE total_invested_usd > 0
               ORDER BY last_updated DESC"""
        )
        open_positions = [
            {
                "token_id": r["token_id"],
                "condition_id": r["condition_id"],
                "total_invested_usd": r["total_invested_usd"],
                "total_tokens": r["total_tokens"],
                "last_updated": r["last_updated"],
            }
            for r in rows
        ]

        # recent trades
        rows = await self._query(
            """SELECT ct.amount_usd, ct.side, ct.status,
                      ct.filled_amount, ct.filled_price, ct.created_at,
                      ts.market_question, ts.outcome, ts.whale_address
               FROM copy_trades ct
               JOIN trade_signals ts ON ct.signal_id = ts.id
               ORDER BY ct.id DESC LIMIT 20"""
        )
        recent_trades = [
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

        # signal counts (last 24h)
        rows = await self._query(
            """SELECT action, COUNT(*) as cnt
               FROM trade_signals
               WHERE created_at >= datetime('now', '-24 hours')
               GROUP BY action"""
        )
        signal_counts = {r["action"]: r["cnt"] for r in rows}

        # spend history (last 7 days)
        rows = await self._query(
            """SELECT date, trade_count, total_spend_usd
               FROM daily_risk
               WHERE date >= date('now', '-6 days')
               ORDER BY date ASC"""
        )
        spend_history = [
            {
                "date": r["date"],
                "trade_count": r["trade_count"],
                "total_spend_usd": r["total_spend_usd"],
            }
            for r in rows
        ]

        return {
            "uptime_secs": uptime_secs,
            "last_block": last_block,
            "trade_count": trade_count,
            "total_spend": total_spend,
            "max_daily_trades": self._config.risk.max_daily_trades,
            "max_daily_spend": self._config.risk.max_daily_spend_usd,
            "max_open_positions": self._config.risk.max_open_positions,
            "open_positions": open_positions,
            "recent_trades": recent_trades,
            "signal_counts": signal_counts,
            "spend_history": spend_history,
        }

    async def _handle_index(self, request: web.Request) -> web.Response:
        data = await self._collect_data()
        html = render_dashboard(data)
        return web.Response(text=html, content_type="text/html")

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def run(self, host: str = "0.0.0.0") -> None:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/health", self._handle_health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, self.port)
        await site.start()
        logger.info("Dashboard running on http://%s:%d", host, self.port)

        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
