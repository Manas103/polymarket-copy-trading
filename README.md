# Polymarket Copy Trading Bot

An automated copy trading system that monitors whale activity on [Polymarket](https://polymarket.com) and mirrors their high-conviction bets in real time. Built on Python with async architecture, it watches Polygon blockchain events, filters through 22 sequential risk checks, and executes trades via Polymarket's CLOB API.

## How It Works

```
Polygon RPC (eth_getLogs)
    |
    v
BlockchainMonitor ── polls every 2s for OrderFilled events
    |                  from CTF Exchange & Neg Risk CTF Exchange
    v
EventParser ──────── decodes EVM logs into OrderFilledEvent structs
    |                  deduplicates by tx_hash:log_index
    v
SignalGenerator ───── checks if maker/taker is a tracked whale
    |                  classifies as COPY_BUY, COPY_SELL, or SKIP
    v
FillAccumulator ──── aggregates small fills over 30-min window
    |                  so conviction check sees total, not fragments
    v
WhaleProfiler ────── fetches whale portfolio value from Data API
    |                  calculates conviction % (trade / portfolio)
    v
WhaleActivityTracker  checks sell/buy ratio over 4h window
    |                  blocks if whale is net-exiting
    v
MarketResolver ────── resolves token_id to market question/outcome
    |                  via Gamma API
    v
TradeFilter ───────── slippage, depth, staleness, time-to-resolution
    |
    v
RiskManager ───────── cooldown, daily limits, position caps
    |
    v
TradeExecutor ─────── builds FAK order with worst-price limit
    |                  submits via CLOB API (py-clob-client)
    v
Repository ────────── persists everything to SQLite
    |
    v
TelegramNotifier ──── sends trade alerts to Telegram
```

## Architecture

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| **BlockchainMonitor** | `src/monitor/blockchain.py` | Polls Polygon RPC for `OrderFilled` events from both Polymarket exchanges |
| **EventParser** | `src/monitor/event_parser.py` | Decodes raw EVM logs into `OrderFilledEvent` structs with deduplication |
| **SignalGenerator** | `src/signal/generator.py` | Classifies events as whale buys/sells, checks minimum trade size ($100) |
| **FillAccumulator** | `src/signal/fill_accumulator.py` | Aggregates small fills per (whale, token) over a rolling time window |
| **WhaleProfiler** | `src/signal/whale_profiler.py` | Fetches portfolio values from Polymarket Data API, calculates conviction % |
| **WhaleActivityTracker** | `src/signal/whale_activity_tracker.py` | Detects whale net-exits by comparing sell/buy volume ratios |
| **ConfluenceDetector** | `src/signal/confluence.py` | Detects when multiple whales buy the same token (position size multiplier) |
| **MarketResolver** | `src/market/resolver.py` | Resolves token IDs to market metadata (question, outcome, end date) via Gamma API |
| **TradeFilter** | `src/signal/filter.py` | Pre-trade checks: slippage, depth, price staleness, time-to-resolution |
| **RiskManager** | `src/risk/manager.py` | Enforces cooldowns, daily trade/spend limits, position caps |
| **CircuitBreaker** | `src/risk/circuit_breaker.py` | Stops all trading after 5 consecutive execution failures, auto-recovers |
| **TradeExecutor** | `src/executor/trade_executor.py` | Builds and submits orders to Polymarket CLOB API |
| **TradingPipeline** | `src/pipeline.py` | Orchestrates the full flow from monitoring to execution |
| **Repository** | `src/persistence/repository.py` | SQLite persistence for events, signals, trades, positions, and risk state |
| **Dashboard** | `src/dashboard/server.py` | In-process aiohttp web dashboard showing bot status and trade history |
| **TelegramNotifier** | `src/notifier/telegram.py` | Sends trade alerts and status messages to Telegram |

### Data Models

| Model | File | Description |
|-------|------|-------------|
| `OrderFilledEvent` | `src/models/events.py` | Decoded on-chain trade event with maker/taker, amounts, token ID |
| `TradeSignal` | `src/models/signals.py` | Generated signal with action (COPY_BUY/SELL/SKIP_*), conviction, market context |
| `CopyTrade` | `src/models/trades.py` | Trade order to submit (token, amount, side, worst price) |
| `TradeResult` | `src/models/trades.py` | Execution result (FILLED/REJECTED/ERROR, filled amount/price) |

## The Filter Pipeline

Every detected whale trade must pass through **22 sequential checks** before execution. If any check fails, the trade is skipped with a specific reason code.

### Signal Generation Filters

| # | Filter | Threshold | Rejects When |
|---|--------|-----------|--------------|
| 1 | Whale Detection | Address in `WHALE_ADDRESSES` | Trader is not a tracked whale |
| 2 | Min Trade Size | >= $100 | Whale's trade is too small |
| 3 | Sell Position Check | Must hold token | Whale sells but we don't hold a position |
| 4 | Exchange Taker | Taker != exchange address | Internal/summary events (noise) |
| 5 | Deduplication | `tx_hash:log_index` unique | Already processed this event |

### Conviction & Activity Filters

| # | Filter | Threshold | Rejects When |
|---|--------|-----------|--------------|
| 6 | Fill Accumulator | 30-min window, 1-hr cooldown | Signal already fired for this (whale, token) pair |
| 7 | **Conviction Check** | >= 1% of whale portfolio | Trade too small relative to whale's total portfolio |
| 8 | Whale Activity | sell/buy ratio <= 1.5 (4h) | Whale is net-exiting despite individual buys |

### Market & Liquidity Filters

| # | Filter | Threshold | Rejects When |
|---|--------|-----------|--------------|
| 9 | Market Resolution | API success | Can't resolve token to market metadata |
| 10 | Orderbook Exists | Has ask orders | No orderbook data available |
| 11 | Price Not Resolved | Best ask < $0.99 | Market already effectively settled |
| 12 | Ask Liquidity | Size > 0 | No liquidity on the ask side |
| 13 | Slippage | <= 2.0% | Our buy price too far above whale's fill price |
| 14 | Time to Resolution | >= 24 hours | Market resolves too soon |
| 15 | Orderbook Depth | >= 2x order size | Not enough depth for our order |
| 16 | Price Staleness | <= 3% (age-adjusted) | Price drifted since whale traded |

### Risk Management Filters

| # | Filter | Threshold | Rejects When |
|---|--------|-----------|--------------|
| 17 | Cooldown | 2 seconds | Last trade was too recent |
| 18 | Daily Trade Count | <= 50/day | Hit daily trade limit |
| 19 | Daily Spend | <= $10/day | Hit daily spend cap |
| 20 | Position Per Market | <= $5/market | Already at max position in this market |
| 21 | Open Positions | <= 2 open | Too many open positions |
| 22 | Circuit Breaker | Not OPEN | 5+ consecutive execution failures |

### Conviction Check (Filter #7) — The Key Filter

This is the most important filter. It prevents the bot from copying trivial trades:

```
conviction_pct = (trade_usd / whale_portfolio_value) * 100

Pass if: conviction_pct >= 1.0%
```

If a whale has a $5M portfolio, only trades >= $50,000 pass. This means tracking whales with multi-million dollar portfolios results in very few signals — the bot needs whales who make concentrated bets relative to their portfolio size.

The fill accumulator helps by aggregating small fills: if a whale makes 10 fills of $20 each over 30 minutes, the conviction check sees $200 total instead of each $20 individually.

## Position Sizing

### Flat Sizing (Default)
Every trade is a flat **$5** (`copy_amount_usd`).

### Dynamic Conviction Scaling (Optional)
When `conviction_scaling_enabled=True`, position size scales linearly:
- 1% conviction -> $5 (min)
- 10% conviction -> $25 (max)
- Linear interpolation between

### Multi-Whale Confluence (Optional)
When `confluence_enabled=True`, if multiple whales buy the same token within 5 minutes:
- 2 whales -> 2.0x position size
- 3 whales -> 2.5x position size
- Capped at 3.0x

## Trade Execution

Orders are submitted as **Fill-And-Kill (FAK)** via Polymarket's CLOB API:
- **Buy**: worst price = `best_ask * (1 + slippage%)`, capped at $0.99
- **Sell**: worst price = `best_bid * (1 - slippage%)`, floored at $0.01
- FAK means: fill what you can immediately, cancel the rest

### Circuit Breaker
Protects against cascading failures:
- **CLOSED** (normal): all trades allowed
- **OPEN** (after 5 failures): all trades blocked for 60 seconds
- **HALF_OPEN** (recovery): 1 test trade allowed; success resets to CLOSED

## Sell / Exit Strategy

The bot automatically exits positions when a tracked whale sells:
1. Whale sell detected on a token we hold
2. Skip accumulator/conviction/activity checks (exit immediately)
3. Fetch orderbook for sell pricing
4. Build sell order for our full position
5. Submit and close position in DB

## APIs Used

| API | Base URL | Purpose |
|-----|----------|---------|
| **Polygon RPC** | Alchemy/custom | `eth_getLogs` for OrderFilled events |
| **Gamma API** | `https://gamma-api.polymarket.com` | Market metadata (question, outcome, end date) |
| **Data API** | `https://data-api.polymarket.com` | Whale portfolio values (`/value?user=`) |
| **CLOB API** | `https://clob.polymarket.com` | Orderbook data and trade submission |
| **Telegram API** | `https://api.telegram.org` | Trade notifications |

## Monitored Contracts

| Contract | Address | Purpose |
|----------|---------|---------|
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` | Standard conditional token trading |
| Neg Risk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` | Negative risk market trading |

The bot watches for the `OrderFilled` event signature on both contracts:
```
0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6
```

## Database Schema

SQLite database (`copytrade.db`) with these tables:

| Table | Purpose |
|-------|---------|
| `whale_events` | All parsed OrderFilled events (dedup, activity queries) |
| `trade_signals` | All signals — passed and rejected (audit trail) |
| `trades` | Executed trades with results (portfolio tracking) |
| `positions` | Current open positions (risk checks) |
| `daily_risk` | Daily trade count and spend (daily limits) |
| `accumulator_fills` | Fill accumulation state (restart recovery) |
| `accumulator_fired` | Signal cooldown state (restart recovery) |
| `block_cursor` | Last processed block number (resume after restart) |

Automatic housekeeping deletes events older than 3 days every 6 hours.

## Configuration

All configuration is via `config.py` dataclasses with environment variable overrides.

### Environment Variables (`.env`)

```env
# Polygon RPC
POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY

# Wallet (EOA private key, no 0x prefix)
PRIVATE_KEY=your_private_key_here

# CLOB API credentials (from Polymarket)
CLOB_API_URL=https://clob.polymarket.com
CLOB_API_KEY=your_api_key
CLOB_API_SECRET=your_api_secret
CLOB_API_PASSPHRASE=your_passphrase

# Whale addresses to track (comma-separated)
WHALE_ADDRESSES=0xabc...,0xdef...

# Telegram notifications (optional)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Dashboard port (optional, default 8080)
DASHBOARD_PORT=8080
```

### Tunable Parameters

#### Blockchain Polling
| Parameter | Default | Description |
|-----------|---------|-------------|
| `poll_interval_seconds` | 2.0 | Seconds between RPC polls |
| `reorg_safety_blocks` | 2 | Blocks behind latest to avoid reorgs |
| `max_blocks_per_query` | 10 | Max blocks per `eth_getLogs` call |

#### Trading
| Parameter | Default | Description |
|-----------|---------|-------------|
| `copy_amount_usd` | 5.0 | Base copy trade size |
| `max_copy_amount_usd` | 5.0 | Maximum copy trade size |
| `max_slippage_pct` | 2.0 | Max allowed slippage vs whale price |
| `min_whale_trade_usd` | 100.0 | Minimum whale trade to generate signal |
| `order_type` | FAK | Fill-And-Kill order type |
| `min_conviction_pct` | 1.0 | Min trade/portfolio ratio to copy |
| `min_hours_to_resolution` | 24.0 | Skip markets resolving within this time |
| `min_depth_multiplier` | 2.0 | Required depth as multiple of order size |
| `max_price_movement_pct` | 3.0 | Max price drift since whale traded |

#### Fill Accumulator
| Parameter | Default | Description |
|-----------|---------|-------------|
| `fill_accumulator_enabled` | True | Aggregate small fills before conviction check |
| `fill_accumulator_window_seconds` | 1800 | Rolling window for fill aggregation (30 min) |
| `fill_accumulator_cooldown_seconds` | 3600 | Cooldown after signal fires (1 hour) |

#### Whale Activity
| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_sell_to_buy_ratio` | 1.5 | Max sell/buy ratio before blocking |
| `activity_window_hours` | 4.0 | Lookback window for activity analysis |

#### Multi-Whale Confluence
| Parameter | Default | Description |
|-----------|---------|-------------|
| `confluence_enabled` | False | Enable multi-whale position multiplier |
| `confluence_window_seconds` | 300 | Time window for confluence detection (5 min) |
| `confluence_min_whales` | 2 | Min whales for confluence trigger |
| `confluence_multiplier` | 2.0 | Position multiplier when confluence fires |
| `confluence_max_multiplier` | 3.0 | Max position multiplier |

#### Risk Limits
| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_daily_trades` | 50 | Max trades per day |
| `max_daily_spend_usd` | 10.0 | Max USD spent per day |
| `max_position_per_market_usd` | 5.0 | Max position per market |
| `max_open_positions` | 2 | Max concurrent open positions |
| `cooldown_seconds` | 2.0 | Min seconds between trades |

#### Circuit Breaker
| Parameter | Default | Description |
|-----------|---------|-------------|
| `failure_threshold` | 5 | Consecutive failures before opening |
| `recovery_timeout_seconds` | 60.0 | Seconds in OPEN before testing recovery |
| `half_open_max_calls` | 1 | Test calls allowed in HALF_OPEN state |

## Setup

### Prerequisites
- Python 3.11+
- Polygon wallet with USDC.e balance
- Polymarket CLOB API credentials
- Alchemy (or other) Polygon RPC endpoint

### Wallet Setup

The bot uses an **EOA (Externally Owned Account)** wallet directly — not a Polymarket proxy wallet.

1. Generate a wallet and fund it with USDC.e and POL on Polygon
2. Polymarket uses **USDC.e** (bridged): `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
   - Not native USDC (`0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`)
3. Approve 6 contracts (one-time on-chain transactions):

**ERC20 approve for USDC.e:**
- CTF Exchange: `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`
- Neg Risk CTF Exchange: `0xC5d563A36AE78145C45a50134d48A1215220f80a`
- Neg Risk Adapter: `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`

**ERC1155 setApprovalForAll for Conditional Tokens (`0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`):**
- Same 3 contracts above

### Installation

```bash
pip install -r requirements.txt
```

### Running

```bash
python -m src.app
```

The bot starts monitoring immediately and serves a dashboard at `http://localhost:8080`.

## Dashboard

The in-process web dashboard (port 8080) shows:
- Bot uptime and last processed block
- Daily trade count and spend vs limits
- Open positions
- Recent trade history with fill details
- Signal action breakdown (which filters are rejecting trades)
- Active fill accumulations in progress
- Daily spend history chart

## Resilience Features

- **Auto-restart**: Pipeline crashes are caught and restarted after 5 seconds
- **Graceful degradation**: If portfolio API is unavailable, conviction check passes (allows trade)
- **Circuit breaker**: Auto-stops trading after 5 consecutive failures, recovers after 60s
- **Triple deduplication**: In-memory set + DB insert check + dedup key restoration on startup
- **State persistence**: Block cursor, accumulator state, and dedup keys survive restarts
- **RPC timeout**: 30s timeout on all RPC calls prevents indefinite hangs
- **DB housekeeping**: Auto-deletes old events every 6 hours to control database size

## Dependencies

```
web3>=7.0            # Polygon RPC interaction (AsyncWeb3)
py-clob-client>=0.1.8  # Polymarket CLOB API client
aiohttp>=3.9         # HTTP client (API calls) + dashboard server
aiosqlite>=0.20      # Async SQLite for persistence
python-dotenv>=1.0   # .env file loading
```

## Project Structure

```
.
├── config.py                          # All configuration dataclasses
├── requirements.txt                   # Python dependencies
├── src/
│   ├── app.py                         # Entry point, component wiring
│   ├── pipeline.py                    # Main orchestration loop
│   ├── models/
│   │   ├── events.py                  # OrderFilledEvent dataclass
│   │   ├── signals.py                 # TradeSignal, SignalAction enum
│   │   ├── trades.py                  # CopyTrade, TradeResult
│   │   └── market.py                  # MarketInfo, OrderBookSnapshot
│   ├── monitor/
│   │   ├── blockchain.py              # Polygon RPC polling
│   │   └── event_parser.py            # EVM log decoding
│   ├── signal/
│   │   ├── generator.py               # Whale buy/sell classification
│   │   ├── filter.py                  # Pre-trade risk checks
│   │   ├── whale_profiler.py          # Portfolio value & conviction
│   │   ├── whale_activity_tracker.py  # Net-exit detection
│   │   ├── fill_accumulator.py        # Small fill aggregation
│   │   └── confluence.py              # Multi-whale detection
│   ├── market/
│   │   ├── resolver.py                # Gamma API market resolution
│   │   └── cache.py                   # TTL cache utility
│   ├── executor/
│   │   ├── trade_executor.py          # Order building & submission
│   │   └── clob_wrapper.py            # Async CLOB API wrapper
│   ├── risk/
│   │   ├── manager.py                 # Daily limits, position caps
│   │   └── circuit_breaker.py         # Failure protection
│   ├── persistence/
│   │   ├── database.py                # SQLite connection management
│   │   └── repository.py             # All DB queries
│   ├── dashboard/
│   │   ├── server.py                  # aiohttp web server
│   │   └── template.py               # HTML template rendering
│   └── notifier/
│       └── telegram.py                # Telegram Bot API notifications
└── tests/                             # Test suite (123 tests)
```
