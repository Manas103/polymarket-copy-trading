"""Tests for ConfluenceDetector: multi-whale buy detection."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from config import AppConfig, TradingConfig
from src.signal.confluence import ConfluenceDetector


class TestConfluenceDetector:
    def setup_method(self):
        self.config = AppConfig(
            trading=TradingConfig(
                confluence_enabled=True,
                confluence_window_seconds=300,
                confluence_min_whales=2,
                confluence_multiplier=2.0,
                confluence_max_multiplier=3.0,
            )
        )
        self.detector = ConfluenceDetector(self.config)

    def test_single_whale_no_boost(self):
        """Single whale buy -> multiplier 1.0."""
        self.detector.record_buy("token_1", "0xWHALE1")
        result = self.detector.check_confluence("token_1")
        assert result.whale_count == 1
        assert result.multiplier == 1.0

    def test_two_whales_boost(self):
        """Two different whales -> 2x multiplier."""
        self.detector.record_buy("token_1", "0xWHALE1")
        self.detector.record_buy("token_1", "0xWHALE2")
        result = self.detector.check_confluence("token_1")
        assert result.whale_count == 2
        assert result.multiplier == pytest.approx(2.0)

    def test_three_whales_higher_boost(self):
        """Three different whales -> 2.5x multiplier."""
        self.detector.record_buy("token_1", "0xWHALE1")
        self.detector.record_buy("token_1", "0xWHALE2")
        self.detector.record_buy("token_1", "0xWHALE3")
        result = self.detector.check_confluence("token_1")
        assert result.whale_count == 3
        assert result.multiplier == pytest.approx(2.5)

    def test_same_whale_twice_no_boost(self):
        """Same whale buying twice -> still 1 unique whale, no boost."""
        self.detector.record_buy("token_1", "0xWHALE1")
        self.detector.record_buy("token_1", "0xWHALE1")
        result = self.detector.check_confluence("token_1")
        assert result.whale_count == 1
        assert result.multiplier == 1.0

    def test_expired_entries_pruned(self):
        """Buys outside the window should be pruned."""
        # Record a buy at time T
        self.detector.record_buy("token_1", "0xWHALE1")

        # Advance time beyond window
        with patch("time.monotonic", return_value=time.monotonic() + 301):
            # Record second whale
            self.detector.record_buy("token_1", "0xWHALE2")
            result = self.detector.check_confluence("token_1")
            # First whale should be pruned
            assert result.whale_count == 1
            assert result.multiplier == 1.0

    def test_disabled_returns_one(self):
        """When confluence disabled, always returns multiplier 1.0."""
        config = AppConfig(
            trading=TradingConfig(confluence_enabled=False)
        )
        detector = ConfluenceDetector(config)
        detector.record_buy("token_1", "0xWHALE1")
        detector.record_buy("token_1", "0xWHALE2")
        result = detector.check_confluence("token_1")
        assert result.multiplier == 1.0

    def test_multiplier_capped_at_max(self):
        """Many whales -> multiplier capped at max_multiplier."""
        for i in range(10):
            self.detector.record_buy("token_1", f"0xWHALE{i}")
        result = self.detector.check_confluence("token_1")
        assert result.whale_count == 10
        assert result.multiplier == pytest.approx(3.0)  # Capped
