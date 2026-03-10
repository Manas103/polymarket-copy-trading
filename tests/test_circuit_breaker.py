"""Tests for CircuitBreaker: state transitions, timeout recovery."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from config import CircuitBreakerConfig
from src.risk.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    def setup_method(self):
        self.config = CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout_seconds=0.5,
            half_open_max_calls=1,
        )
        self.cb = CircuitBreaker(self.config, name="test")

    def test_initial_state_closed(self):
        assert self.cb.state == CircuitState.CLOSED
        assert self.cb.can_execute() is True

    def test_stays_closed_under_threshold(self):
        self.cb.record_failure()
        self.cb.record_failure()
        assert self.cb.state == CircuitState.CLOSED
        assert self.cb.can_execute() is True

    def test_trips_at_threshold(self):
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == CircuitState.OPEN
        assert self.cb.can_execute() is False

    def test_success_resets_count(self):
        self.cb.record_failure()
        self.cb.record_failure()
        self.cb.record_success()
        # After success, failure count resets
        self.cb.record_failure()
        self.cb.record_failure()
        assert self.cb.state == CircuitState.CLOSED

    def test_recovery_timeout_to_half_open(self):
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == CircuitState.OPEN

        # Simulate time passing beyond recovery timeout
        self.cb._last_failure_time = time.monotonic() - 1.0
        assert self.cb.state == CircuitState.HALF_OPEN
        assert self.cb.can_execute() is True

    def test_half_open_success_closes(self):
        for _ in range(3):
            self.cb.record_failure()
        self.cb._last_failure_time = time.monotonic() - 1.0

        # Trigger transition to HALF_OPEN
        assert self.cb.state == CircuitState.HALF_OPEN

        self.cb.record_success()
        assert self.cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        for _ in range(3):
            self.cb.record_failure()
        self.cb._last_failure_time = time.monotonic() - 1.0

        # Trigger HALF_OPEN
        assert self.cb.state == CircuitState.HALF_OPEN

        self.cb.record_failure()
        assert self.cb.state == CircuitState.OPEN

    def test_half_open_max_calls(self):
        for _ in range(3):
            self.cb.record_failure()
        self.cb._last_failure_time = time.monotonic() - 1.0

        # HALF_OPEN allows 1 call
        assert self.cb.can_execute() is True

        # Simulate that one call was used
        self.cb._half_open_calls = 1
        assert self.cb.can_execute() is False
