"""Circuit breaker for storage reliability and outage protection.

Per AGENTS.md §Phase 7:
- Three states: CLOSED, OPEN, HALF_OPEN
- N consecutive failures opens the breaker
- Cooldown period permits a single probe in HALF_OPEN
- Success closes the breaker; failure re-opens it
- Emits observer.on_breaker_state transitions
"""

from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.clock import Clock, SystemClock
from app.core.errors import StorageUnavailable
from app.core.observer import NullObserver, Observer
from app.core.storage.base import Storage, Transaction

T = TypeVar("T")


class BreakerState(enum.StrEnum):
    """Circuit breaker operational states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker protecting storage backends against cascade failure.

    Args:
        clock: Time source (defaults to SystemClock, injectable for tests).
        observer: Observer for state change events.
        failure_threshold: Consecutive failures before opening the circuit (default 3).
        cooldown_ms: Time in ms to wait before testing recovery in half-open state (default 5000).
    """

    def __init__(
        self,
        clock: Clock | None = None,
        observer: Observer | None = None,
        failure_threshold: int = 3,
        cooldown_ms: int = 5000,
    ) -> None:
        self._clock: Clock = clock or SystemClock()
        self._observer: Observer = observer or NullObserver()
        self._failure_threshold: int = failure_threshold
        self._cooldown_ms: int = cooldown_ms

        self._state: BreakerState = BreakerState.CLOSED
        self._consecutive_failures: int = 0
        self._last_failure_time_ms: int = 0

    @property
    def state(self) -> BreakerState:
        """Current breaker state."""
        return self._state

    @property
    def consecutive_failures(self) -> int:
        """Current consecutive failure count."""
        return self._consecutive_failures

    @property
    def failure_threshold(self) -> int:
        """Configured failure threshold."""
        return self._failure_threshold

    @property
    def cooldown_ms(self) -> int:
        """Configured cooldown duration in milliseconds."""
        return self._cooldown_ms

    async def can_execute(self) -> bool:
        """Check if an execution is permitted under the current breaker state.

        Transitions from OPEN to HALF_OPEN if cooldown has elapsed.

        Returns:
            True if call should proceed, False if circuit is OPEN.
        """
        if self._state == BreakerState.CLOSED:
            return True

        now = await self._clock.now_ms()

        if self._state == BreakerState.OPEN:
            if now - self._last_failure_time_ms >= self._cooldown_ms:
                self._transition_to(BreakerState.HALF_OPEN)
                return True
            return False

        # In HALF_OPEN state, permit the probe
        return True

    async def record_success(self) -> None:
        """Record a successful operation.

        Closes the circuit if half-open, and resets failure count.
        """
        if self._state != BreakerState.CLOSED:
            self._transition_to(BreakerState.CLOSED)
        self._consecutive_failures = 0

    async def record_failure(self) -> None:
        """Record a failed operation.

        Opens the circuit if threshold reached or if a probe in half-open failed.
        """
        self._consecutive_failures += 1
        self._last_failure_time_ms = await self._clock.now_ms()

        if self._state == BreakerState.HALF_OPEN:
            # Probe failed — immediately trip back to OPEN
            self._transition_to(BreakerState.OPEN)
        elif (
            self._state == BreakerState.CLOSED
            and self._consecutive_failures >= self._failure_threshold
        ):
            self._transition_to(BreakerState.OPEN)

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED state."""
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time_ms = 0
        self._observer.on_breaker_state(BreakerState.CLOSED.value)

    def _transition_to(self, new_state: BreakerState) -> None:
        """Update breaker state and notify observer."""
        if self._state != new_state:
            self._state = new_state
            self._observer.on_breaker_state(new_state.value)

    async def execute(self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Execute an async callable protected by the circuit breaker.

        Raises:
            StorageUnavailable: If the circuit is OPEN or the call fails with a storage error.
        """
        if not await self.can_execute():
            raise StorageUnavailable("Storage circuit breaker is OPEN")

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except StorageUnavailable:
            await self.record_failure()
            raise
        except Exception as exc:
            await self.record_failure()
            raise StorageUnavailable(f"Storage operation failed: {exc}") from exc


class CircuitBreakerStorage(Storage):
    """Storage adapter that wraps an underlying Storage with a CircuitBreaker."""

    def __init__(self, storage: Storage, breaker: CircuitBreaker) -> None:
        self._storage = storage
        self._breaker = breaker

    @property
    def breaker(self) -> CircuitBreaker:
        """Access the underlying circuit breaker."""
        return self._breaker

    @property
    def underlying_storage(self) -> Storage:
        """Access the wrapped storage backend."""
        return self._storage

    async def now_ms(self) -> int:
        """Return current time in milliseconds."""
        return await self._breaker.execute(self._storage.now_ms)

    async def incr_expiring(self, key: str, by: int, ttl_ms: int) -> int:
        """Atomic increment with TTL under circuit breaker."""
        return await self._breaker.execute(self._storage.incr_expiring, key, by, ttl_ms)

    async def incr_expiring_peek(
        self, key: str, by: int, ttl_ms: int, peek_key: str
    ) -> tuple[int, int]:
        """Atomic increment + peek under circuit breaker."""
        return await self._breaker.execute(
            self._storage.incr_expiring_peek, key, by, ttl_ms, peek_key
        )

    async def decr(self, key: str, by: int) -> int:
        """Atomic decrement under circuit breaker."""
        return await self._breaker.execute(self._storage.decr, key, by)

    async def optimistic(self, watch_keys: list[str]) -> Transaction:
        """Begin optimistic transaction under circuit breaker."""
        return await self._breaker.execute(self._storage.optimistic, watch_keys)

    async def delete(self, key: str) -> bool:
        """Delete key under circuit breaker."""
        return await self._breaker.execute(self._storage.delete, key)

    async def ping(self) -> bool:
        """Ping backend under circuit breaker."""
        if not await self._breaker.can_execute():
            return False
        try:
            res = await self._storage.ping()
            if res:
                await self._breaker.record_success()
                return True
            await self._breaker.record_failure()
            return False
        except Exception:
            await self._breaker.record_failure()
            return False

    async def close(self) -> None:
        """Close wrapped storage."""
        await self._storage.close()
