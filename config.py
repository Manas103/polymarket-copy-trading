"""Configuration dataclasses for the copy trading system."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Contract addresses
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# OrderFilled event signature (same ABI on both exchanges)
ORDER_FILLED_EVENT_SIG = (
    "0xd0a08e8c493f9c94f29311604c9de1d4e1f89571f2882a4e7b2e3b1685ac1459"
)


@dataclass(frozen=True)
class PolygonConfig:
    rpc_url: str = field(
        default_factory=lambda: os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
    )
    poll_interval_seconds: float = 2.0
    reorg_safety_blocks: int = 2
    max_blocks_per_query: int = 10
    ctf_exchange: str = CTF_EXCHANGE
    neg_risk_ctf_exchange: str = NEG_RISK_CTF_EXCHANGE
    order_filled_event_sig: str = ORDER_FILLED_EVENT_SIG


@dataclass(frozen=True)
class ClobConfig:
    api_url: str = field(
        default_factory=lambda: os.getenv(
            "CLOB_API_URL", "https://clob.polymarket.com"
        )
    )
    api_key: str = field(default_factory=lambda: os.getenv("CLOB_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("CLOB_API_SECRET", ""))
    api_passphrase: str = field(
        default_factory=lambda: os.getenv("CLOB_API_PASSPHRASE", "")
    )
    private_key: str = field(default_factory=lambda: os.getenv("PRIVATE_KEY", ""))
    chain_id: int = 137
    signature_type: int | None = None  # None = EOA (direct wallet), 1 = proxy


@dataclass(frozen=True)
class GammaConfig:
    api_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
    cache_ttl_seconds: int = 300


@dataclass(frozen=True)
class TradingConfig:
    copy_amount_usd: float = 5.0
    max_copy_amount_usd: float = 5.0
    max_slippage_pct: float = 2.0
    min_whale_trade_usd: float = 100.0
    order_type: str = "FAK"  # Fill-And-Kill

    # Dynamic position sizing
    conviction_scaling_enabled: bool = False
    conviction_base_pct: float = 1.0
    conviction_max_pct: float = 10.0

    # Conviction filter: skip trades that are < N% of whale's portfolio
    min_conviction_pct: float = 1.0
    portfolio_cache_ttl_seconds: int = 300

    # Time-to-resolution filter
    min_hours_to_resolution: float = 24.0

    # Orderbook depth filter
    min_depth_multiplier: float = 2.0

    # Price movement / staleness filter
    max_price_movement_pct: float = 3.0

    # Whale sell-to-buy ratio detection
    max_sell_to_buy_ratio: float = 1.5
    activity_window_hours: float = 4.0

    # Multi-whale confluence
    confluence_enabled: bool = False
    confluence_window_seconds: int = 300
    confluence_min_whales: int = 2
    confluence_multiplier: float = 2.0
    confluence_max_multiplier: float = 3.0


@dataclass(frozen=True)
class RiskConfig:
    max_daily_trades: int = 50
    max_daily_spend_usd: float = 10.0
    max_position_per_market_usd: float = 5.0
    max_open_positions: int = 2
    cooldown_seconds: float = 2.0


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    half_open_max_calls: int = 1


@dataclass(frozen=True)
class WhaleConfig:
    addresses: list[str] = field(
        default_factory=lambda: [
            addr.strip()
            for addr in os.getenv("WHALE_ADDRESSES", "").split(",")
            if addr.strip()
        ]
    )


@dataclass(frozen=True)
class DatabaseConfig:
    path: str = field(
        default_factory=lambda: os.getenv("DB_PATH", "copytrade.db")
    )


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))


@dataclass(frozen=True)
class AppConfig:
    polygon: PolygonConfig = field(default_factory=PolygonConfig)
    clob: ClobConfig = field(default_factory=ClobConfig)
    gamma: GammaConfig = field(default_factory=GammaConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    whales: WhaleConfig = field(default_factory=WhaleConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
