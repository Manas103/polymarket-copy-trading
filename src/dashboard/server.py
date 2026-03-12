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

    def __init__(self, config: AppConfig, db_path: str) -> None:
        self._config = config
        self._db_path = db_path
        self._start_time = time.time()
        self.port = int(os.getenv("DASHBOARD_PORT", "8080"))

    async def _collect_data(self) -> dict:
        """Open a fresh connection, run all reads, close it."""
        uptime_secs = time.time() - self._start_time

        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA query_only=ON")

            # last block
            cursor = await conn.execute(
                "SELECT last_block FROM block_cursor WHERE id = 1"
            )
            row = await cursor.fetchone()
            last_block = row["last_block"] if row else None

            # daily risk
            today = date.today().isoformat()
            cursor = await conn.execute(
                "SELECT trade_count, total_spend_usd FROM daily_risk WHERE date = ?",
                (today,),
            )
            row = await cursor.fetchone()
            trade_count = row["trade_count"] if row else 0
            total_spend = row["total_spend_usd"] if row else 0.0

            # open positions
            cursor = await conn.execute(
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
                for r in await cursor.fetchall()
            ]

            # recent trades
            cursor = await conn.execute(
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
                for r in await cursor.fetchall()
            ]

            # signal counts (last 24h)
            cursor = await conn.execute(
                """SELECT action, COUNT(*) as cnt
                   FROM trade_signals
                   WHERE created_at >= datetime('now', '-24 hours')
                   GROUP BY action"""
            )
            signal_counts = {r["action"]: r["cnt"] for r in await cursor.fetchall()}

            # spend history (last 7 days)
            cursor = await conn.execute(
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
                for r in await cursor.fetchall()
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
        try:
            data = await self._collect_data()
            html = render_dashboard(data)
            return web.Response(text=html, content_type="text/html")
        except Exception:
            logger.exception("Dashboard render failed")
            return web.Response(
                text="<h1>Dashboard temporarily unavailable</h1><p>Check logs. Auto-retry in 15s.</p>"
                     '<meta http-equiv="refresh" content="15">',
                content_type="text/html",
                status=503,
            )

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
