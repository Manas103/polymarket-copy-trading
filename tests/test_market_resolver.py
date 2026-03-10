"""Tests for MarketResolver: cache, Gamma API, orderbook parsing."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from aioresponses import aioresponses

from config import AppConfig, GammaConfig
from src.market.cache import TTLCache
from src.market.resolver import MarketResolver
from src.models.market import MarketInfo


class TestTTLCache:
    def test_set_and_get(self):
        cache: TTLCache[str] = TTLCache(ttl_seconds=60)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_cache_miss(self):
        cache: TTLCache[str] = TTLCache(ttl_seconds=60)
        assert cache.get("missing") is None

    def test_ttl_expiry(self):
        cache: TTLCache[str] = TTLCache(ttl_seconds=0)
        cache.set("key1", "value1")
        # TTL=0 means immediately expired
        assert cache.get("key1") is None

    def test_len(self):
        cache: TTLCache[str] = TTLCache(ttl_seconds=60)
        cache.set("a", "1")
        cache.set("b", "2")
        assert len(cache) == 2

    def test_clear(self):
        cache: TTLCache[str] = TTLCache(ttl_seconds=60)
        cache.set("a", "1")
        cache.clear()
        assert cache.get("a") is None


class TestMarketResolver:
    @pytest_asyncio.fixture
    async def resolver(self):
        config = AppConfig(gamma=GammaConfig(cache_ttl_seconds=60))
        r = MarketResolver(config)
        await r.start()
        yield r
        await r.stop()

    @pytest.mark.asyncio
    async def test_resolve_market_gamma_api(self, resolver: MarketResolver):
        gamma_response = [
            {
                "condition_id": "0xcond123",
                "question": "Will BTC hit 100k?",
                "neg_risk": False,
                "active": True,
                "end_date_iso": "2025-12-31",
                "tokens": [
                    {"token_id": "tok_abc", "outcome": "Yes"},
                    {"token_id": "tok_def", "outcome": "No"},
                ],
            }
        ]

        with aioresponses() as m:
            m.get(
                "https://gamma-api.polymarket.com/markets?clob_token_ids=tok_abc",
                payload=gamma_response,
            )
            info = await resolver.resolve_market("tok_abc")

        assert info is not None
        assert info.question == "Will BTC hit 100k?"
        assert info.outcome == "Yes"
        assert info.condition_id == "0xcond123"

    @pytest.mark.asyncio
    async def test_resolve_market_cached(self, resolver: MarketResolver):
        """Second call should use cache, no HTTP request."""
        gamma_response = [
            {
                "condition_id": "0xcond456",
                "question": "Test market",
                "tokens": [{"token_id": "tok_cached", "outcome": "Yes"}],
            }
        ]

        with aioresponses() as m:
            m.get(
                "https://gamma-api.polymarket.com/markets?clob_token_ids=tok_cached",
                payload=gamma_response,
            )
            info1 = await resolver.resolve_market("tok_cached")

        # Second call - no mock set up, would fail if it tried HTTP
        info2 = await resolver.resolve_market("tok_cached")
        assert info2 is not None
        assert info2.question == "Test market"

    @pytest.mark.asyncio
    async def test_get_orderbook(self, resolver: MarketResolver):
        orderbook_data = {
            "bids": [
                {"price": "0.48", "size": "500"},
                {"price": "0.47", "size": "300"},
            ],
            "asks": [
                {"price": "0.52", "size": "400"},
                {"price": "0.53", "size": "200"},
            ],
        }

        with aioresponses() as m:
            m.get(
                "http://localhost:9999/book?token_id=tok_ob",
                payload=orderbook_data,
            )
            # Override CLOB URL for test
            resolver._clob_url = "http://localhost:9999"
            ob = await resolver.get_orderbook("tok_ob")

        assert ob is not None
        assert ob.best_bid == 0.48
        assert ob.best_ask == 0.52
        assert ob.bid_size == 500.0
        assert ob.ask_size == 400.0
        assert ob.midpoint == pytest.approx(0.50)
        assert ob.spread == pytest.approx(0.04)

    @pytest.mark.asyncio
    async def test_get_orderbook_empty(self, resolver: MarketResolver):
        with aioresponses() as m:
            m.get(
                "http://localhost:9999/book?token_id=tok_empty",
                payload={"bids": [], "asks": []},
            )
            resolver._clob_url = "http://localhost:9999"
            ob = await resolver.get_orderbook("tok_empty")

        assert ob is not None
        assert ob.best_bid is None
        assert ob.best_ask is None
        assert ob.midpoint is None
