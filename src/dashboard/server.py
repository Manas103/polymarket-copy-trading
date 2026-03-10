"""Dashboard web server for monitoring the copy trading bot."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from aiohttp import web

from config import AppConfig
from src.dashboard.template import render_dashboard
from src.persistence.repository import Repository

logger = logging.getLogger(__name__)


class DashboardServer:
    """Lightweight aiohttp dashboard that runs alongside the trading pipeline."""

    def __init__(self, config: AppConfig, repo: Repository) -> None:
        self._config = config
        self._repo = repo
        self._start_time = time.time()
        self.port = int(os.getenv("DASHBOARD_PORT", "8080"))

    async def _collect_data(self) -> dict:
        uptime_secs = time.time() - self._start_time
        last_block = await self._repo.get_last_block()
        trade_count, total_spend = await self._repo.get_daily_risk()
        open_positions = await self._repo.get_all_open_positions()
        recent_trades = await self._repo.get_recent_trades(20)
        signal_counts = await self._repo.get_signal_action_counts(24)
        spend_history = await self._repo.get_daily_spend_history(7)

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
