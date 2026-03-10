"""Application entry point with graceful shutdown."""

from __future__ import annotations

import asyncio
import logging
import sys

from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from config import AppConfig
from src.executor.clob_wrapper import AsyncClobWrapper
from src.executor.trade_executor import TradeExecutor
from src.market.resolver import MarketResolver
from src.monitor.blockchain import BlockchainMonitor
from src.monitor.event_parser import EventParser
from src.persistence.database import Database
from src.persistence.repository import Repository
from src.pipeline import TradingPipeline
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.manager import RiskManager
from src.signal.filter import TradeFilter
from src.signal.confluence import ConfluenceDetector
from src.signal.generator import SignalGenerator
from src.dashboard.server import DashboardServer
from src.notifier.telegram import TelegramNotifier
from src.signal.whale_activity_tracker import WhaleActivityTracker
from src.signal.whale_profiler import WhaleProfiler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def run() -> None:
    config = AppConfig()

    # Validate required config
    if not config.clob.private_key:
        logger.error("PRIVATE_KEY not set in .env")
        sys.exit(1)
    if not config.whales.addresses:
        logger.error("WHALE_ADDRESSES not set in .env")
        sys.exit(1)

    logger.info("Starting Polymarket Copy Trading System")
    logger.info("Tracking %d whale addresses", len(config.whales.addresses))
    logger.info("Copy amount: $%.2f per trade", config.trading.copy_amount_usd)

    # Initialize components
    db = Database(config.database.path)
    await db.connect()
    repo = Repository(db)

    w3 = AsyncWeb3(AsyncHTTPProvider(config.polygon.rpc_url))
    chain_id = await w3.eth.chain_id
    logger.info("Connected to chain %d, RPC: %s", chain_id, config.polygon.rpc_url)

    monitor = BlockchainMonitor(config, w3)
    parser = EventParser()
    signal_gen = SignalGenerator(config, repository=repo)
    trade_filter = TradeFilter(config)

    resolver = MarketResolver(config)
    await resolver.start()

    clob = AsyncClobWrapper(config.clob)
    await clob.connect()

    cb = CircuitBreaker(config.circuit_breaker, name="clob")
    risk_manager = RiskManager(config, repo)
    executor = TradeExecutor(config, clob, cb)

    whale_profiler = WhaleProfiler(config)
    await whale_profiler.start()
    activity_tracker = WhaleActivityTracker(config, repo)
    confluence = ConfluenceDetector(config)

    notifier = TelegramNotifier(config.telegram.bot_token, config.telegram.chat_id)
    await notifier.start()

    pipeline = TradingPipeline(
        config=config,
        monitor=monitor,
        parser=parser,
        signal_gen=signal_gen,
        trade_filter=trade_filter,
        market_resolver=resolver,
        risk_manager=risk_manager,
        executor=executor,
        repository=repo,
        whale_profiler=whale_profiler,
        activity_tracker=activity_tracker,
        confluence=confluence,
        notifier=notifier,
    )

    dashboard = DashboardServer(config=config, repo=repo)
    logger.info("Dashboard: http://localhost:%d", dashboard.port)

    try:
        await asyncio.gather(pipeline.run(), dashboard.run())
    finally:
        logger.info("Shutting down...")
        await notifier.stop()
        await whale_profiler.stop()
        await resolver.stop()
        await db.close()
        logger.info("Shutdown complete")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user (Ctrl+C)")


if __name__ == "__main__":
    main()
