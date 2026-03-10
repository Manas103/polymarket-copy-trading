"""Tests for RiskManager: daily limits, position limits, cooldowns."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio

from config import AppConfig, RiskConfig
from src.persistence.database import Database
from src.persistence.repository import Repository
from src.risk.manager import RiskManager


class TestRiskManager:
    @pytest_asyncio.fixture
    async def risk_manager(self, db: Database) -> RiskManager:
        config = AppConfig(
            risk=RiskConfig(
                max_daily_trades=3,
                max_daily_spend_usd=20.0,
                max_position_per_market_usd=10.0,
                max_open_positions=2,
                cooldown_seconds=0.0,
            ),
        )
        repo = Repository(db)
        return RiskManager(config, repo)

    @pytest.mark.asyncio
    async def test_allowed_trade(self, risk_manager: RiskManager):
        check = await risk_manager.check_trade(5.0, "token_1", "cond_1")
        assert check.allowed is True

    @pytest.mark.asyncio
    async def test_daily_trade_limit(self, risk_manager: RiskManager, repo: Repository):
        # Record 3 trades
        for _ in range(3):
            await repo.record_daily_trade(5.0)

        check = await risk_manager.check_trade(5.0, "token_1", "cond_1")
        assert check.allowed is False
        assert "Daily trade limit" in check.reason

    @pytest.mark.asyncio
    async def test_daily_spend_limit(self, risk_manager: RiskManager, repo: Repository):
        await repo.record_daily_trade(18.0)

        check = await risk_manager.check_trade(5.0, "token_1", "cond_1")
        assert check.allowed is False
        assert "Daily spend limit" in check.reason

    @pytest.mark.asyncio
    async def test_position_limit(self, risk_manager: RiskManager, repo: Repository):
        await repo.upsert_position("token_1", "cond_1", 8.0, 16.0)

        check = await risk_manager.check_trade(5.0, "token_1", "cond_1")
        assert check.allowed is False
        assert "Position limit" in check.reason

    @pytest.mark.asyncio
    async def test_max_open_positions(self, risk_manager: RiskManager, repo: Repository):
        await repo.upsert_position("token_1", "cond_1", 5.0, 10.0)
        await repo.upsert_position("token_2", "cond_2", 5.0, 10.0)

        # New position in new token
        check = await risk_manager.check_trade(5.0, "token_3", "cond_3")
        assert check.allowed is False
        assert "Max open positions" in check.reason

    @pytest.mark.asyncio
    async def test_existing_position_allowed(self, risk_manager: RiskManager, repo: Repository):
        """Adding to existing position should not be blocked by max_open_positions."""
        await repo.upsert_position("token_1", "cond_1", 5.0, 10.0)
        await repo.upsert_position("token_2", "cond_2", 5.0, 10.0)

        # Adding to existing position
        check = await risk_manager.check_trade(5.0, "token_1", "cond_1")
        assert check.allowed is True

    @pytest.mark.asyncio
    async def test_cooldown(self, db: Database):
        config = AppConfig(
            risk=RiskConfig(cooldown_seconds=100.0),
        )
        repo = Repository(db)
        rm = RiskManager(config, repo)
        rm.record_trade()

        check = await rm.check_trade(5.0, "token_1", "cond_1")
        assert check.allowed is False
        assert "Cooldown" in check.reason
