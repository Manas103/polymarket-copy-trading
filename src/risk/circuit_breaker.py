"""Circuit breaker pattern for failure protection."""

from __future__ import annotations

import logging
import time
from enum import Enum

from config import CircuitBreakerConfig

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"          # Normal operation
    OPEN = "OPEN"              # Blocking all calls
    HALF_OPEN = "HALF_OPEN"    # Testing if service recovered


class CircuitBreaker:
    """Circuit breaker: stops calls after repeated failures, auto-recovers."""

    def __init__(self, config: CircuitBreakerConfig, name: str = "default") -> None:
        self._config = config
        self._name = name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        # Check if recovery timeout has passed while OPEN
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._config.recovery_timeout_seconds:
                logger.info("Circuit breaker [%s] transitioning to HALF_OPEN", self._name)
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED

    def can_execute(self) -> bool:
        """Check if a call is allowed."""
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self._config.half_open_max_calls
        return False  # OPEN

    def record_success(self) -> None:
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker [%s] recovered -> CLOSED", self._name)
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_calls = 0
        elif self._state == CircuitState.CLOSED:
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            logger.warning("Circuit breaker [%s] HALF_OPEN failure -> OPEN", self._name)
            self._state = CircuitState.OPEN
            return

        if self._failure_count >= self._config.failure_threshold:
            logger.warning(
                "Circuit breaker [%s] tripped -> OPEN (%d failures)",
                self._name,
                self._failure_count,
            )
            self._state = CircuitState.OPEN
