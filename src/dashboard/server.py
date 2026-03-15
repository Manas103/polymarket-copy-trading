"""In-process aiohttp dashboard — runs on the bot's async event loop.

No subprocess, no synchronous sqlite3. Reads from the shared Repository
to eliminate cross-process SQLite locking entirely.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

from src.dashboard.template import render_dashboard

if TYPE_CHECKING:
    from config import AppConfig
    from src.persistence.repository import Repository
    from src.signal.fill_accumulator import FillAccumulator

logger = logging.getLogger(__name__)


def create_dashboard_app(
    repo: Repository,
    config: AppConfig,
    fill_accumulator: FillAccumulator | None = None,
    start_time: float | None = None,
) -> web.Application:
    """Create an aiohttp Application wired to the shared repo."""
    app = web.Application()
    app["repo"] = repo
    app["config"] = config
    app["fill_accumulator"] = fill_accumulator
    app["start_time"] = start_time or time.time()
    app.router.add_get("/", _handle_index)
    app.router.add_get("/health", _handle_health)
    return app


async def _handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _handle_index(request: web.Request) -> web.Response:
    try:
        data = await _collect_data(request.app)
        html = render_dashboard(data)
        return web.Response(text=html, content_type="text/html")
    except Exception:
        logger.exception("Dashboard render failed")
        return web.Response(
            status=503,
            text=(
                "<h1>Dashboard temporarily unavailable</h1>"
                '<p>Check logs. Auto-retry in 15s.</p>'
                '<meta http-equiv="refresh" content="15">'
            ),
            content_type="text/html",
        )


async def _collect_data(app: web.Application) -> dict:
    """Build the data dict from the shared repository."""
    repo: Repository = app["repo"]
    config: AppConfig = app["config"]
    fill_accumulator: FillAccumulator | None = app["fill_accumulator"]

    uptime_secs = time.time() - app["start_time"]

    last_block = await repo.get_last_block()
    cursor_updated_at = await repo.get_block_cursor_updated_at()
    trade_count, total_spend = await repo.get_daily_risk()
    open_positions = await repo.get_all_open_positions()
    recent_trades = await repo.get_recent_trades()
    signal_counts = await repo.get_signal_action_counts()
    whale_signals = await repo.get_recent_whale_signals()
    spend_history = await repo.get_daily_spend_history()

    if fill_accumulator:
        active_accumulations = fill_accumulator.get_active_accumulations()
    else:
        active_accumulations = []

    return {
        "uptime_secs": uptime_secs,
        "last_block": last_block,
        "cursor_updated_at": cursor_updated_at,
        "trade_count": trade_count,
        "total_spend": total_spend,
        "max_daily_trades": config.risk.max_daily_trades,
        "max_daily_spend": config.risk.max_daily_spend_usd,
        "max_open_positions": config.risk.max_open_positions,
        "open_positions": open_positions,
        "recent_trades": recent_trades,
        "signal_counts": signal_counts,
        "whale_signals": whale_signals,
        "active_accumulations": active_accumulations,
        "fill_window_seconds": config.trading.fill_accumulator_window_seconds,
        "spend_history": spend_history,
    }
