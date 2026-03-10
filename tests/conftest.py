"""Shared test fixtures and mocks."""

from __future__ import annotations

import pytest
import pytest_asyncio

from config import (
    AppConfig,
    CircuitBreakerConfig,
    ClobConfig,
    DatabaseConfig,
    GammaConfig,
    PolygonConfig,
    RiskConfig,
    TradingConfig,
    WhaleConfig,
)
from src.models.events import ExchangeType, OrderFilledEvent
from src.persistence.database import Database
from src.persistence.repository import Repository

WHALE_ADDRESS = "0xABCDEF1234567890abcdef1234567890ABCDEF12"
NON_WHALE_ADDRESS = "0x1111111111111111111111111111111111111111"
CTF_EXCHANGE_ADDR = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE_ADDR = "0xC5d563A36AE78145C45a50134d48A1215220f80a"


@pytest.fixture
def test_config() -> AppConfig:
    return AppConfig(
        polygon=PolygonConfig(
            rpc_url="http://localhost:8545",
            poll_interval_seconds=0.1,
        ),
        clob=ClobConfig(
            api_url="http://localhost:9999",
            private_key="0x" + "ab" * 32,
        ),
        gamma=GammaConfig(cache_ttl_seconds=60),
        trading=TradingConfig(
            copy_amount_usd=5.0,
            max_copy_amount_usd=5.0,
            max_slippage_pct=2.0,
            min_whale_trade_usd=100.0,
            min_conviction_pct=1.0,
            portfolio_cache_ttl_seconds=300,
            min_hours_to_resolution=24.0,
            min_depth_multiplier=2.0,
            max_price_movement_pct=3.0,
            max_sell_to_buy_ratio=1.5,
            activity_window_hours=4.0,
            conviction_scaling_enabled=False,
            conviction_base_pct=1.0,
            conviction_max_pct=10.0,
            confluence_enabled=False,
            confluence_window_seconds=300,
            confluence_min_whales=2,
            confluence_multiplier=2.0,
            confluence_max_multiplier=3.0,
        ),
        risk=RiskConfig(
            max_daily_trades=50,
            max_daily_spend_usd=10.0,
            max_position_per_market_usd=5.0,
            max_open_positions=2,
            cooldown_seconds=0.0,
        ),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout_seconds=1.0,
        ),
        whales=WhaleConfig(addresses=[WHALE_ADDRESS]),
        database=DatabaseConfig(path=":memory:"),
    )


@pytest_asyncio.fixture
async def db(test_config: AppConfig):
    database = Database(test_config.database.path)
    await database.connect()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def repo(db: Database) -> Repository:
    return Repository(db)


def make_event(
    *,
    tx_hash: str = "0xabc123",
    log_index: int = 0,
    block_number: int = 1000,
    block_timestamp: int = 1700000000,
    exchange_type: ExchangeType = ExchangeType.CTF,
    exchange_address: str = CTF_EXCHANGE_ADDR,
    order_hash: str = "0x" + "ff" * 32,
    maker: str = WHALE_ADDRESS,
    taker: str = NON_WHALE_ADDRESS,
    maker_asset_id: int = 0,
    taker_asset_id: int = 123456789,
    maker_amount_filled: int = 500_000_000,
    taker_amount_filled: int = 1_000_000_000,
    fee: int = 5_000_000,
) -> OrderFilledEvent:
    """Helper to create OrderFilledEvent with sensible defaults.

    Default: whale is maker, buying (maker_asset_id=0 means paying USDC).
    $500 USDC for 1000 tokens -> price ~0.50.
    """
    return OrderFilledEvent(
        tx_hash=tx_hash,
        log_index=log_index,
        block_number=block_number,
        block_timestamp=block_timestamp,
        exchange_type=exchange_type,
        exchange_address=exchange_address,
        order_hash=order_hash,
        maker=maker,
        taker=taker,
        maker_asset_id=maker_asset_id,
        taker_asset_id=taker_asset_id,
        maker_amount_filled=maker_amount_filled,
        taker_amount_filled=taker_amount_filled,
        fee=fee,
    )
