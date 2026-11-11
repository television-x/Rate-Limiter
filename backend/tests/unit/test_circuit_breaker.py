"""Unit tests for CircuitBreaker and CircuitBreakerStorage.

Per AGENTS.md §Phase 7:
- Breaker states: closed, open, half-open
- N consecutive failures opens the breaker
- Cooldown period permits a probe in half-open
- Success closes the breaker; failure re-opens it
- Observer emits on_breaker_state transitions
- Uses FakeClock for deterministic timing
"""

from __future__ import annotations

import pytest

from app.core.circuit_breaker import BreakerState, CircuitBreaker, CircuitBreakerStorage
from app.core.clock import FakeClock
from app.core.errors import StorageUnavailable
from app.core.observer import Observer
from app.core.storage.memory import MemoryStorage

pytestmark = pytest.mark.unit


class TrackingObserver(Observer):
    """Observer that records circuit breaker state changes."""

    def __init__(self) -> None:
        self.breaker_states: list[str] = []

    def on_decision(self, policy_id: str, decision: object) -> None:
        pass

    def on_storage_op(self, op: str, seconds: float, ok: bool) -> None:
        pass

    def on_txn_conflict(self, algorithm: str) -> None:
        pass

    def on_txn_retry_exhausted(self, algorithm: str) -> None:
        pass

    def on_breaker_state(self, state: str) -> None:
        self.breaker_states.append(state)


class TestCircuitBreakerStateTransitions:
    """Test the complete state machine transitions."""

    async def test_initial_state_is_closed(self, fake_clock: FakeClock) -> None:
        breaker = CircuitBreaker(clock=fake_clock, failure_threshold=3, cooldown_ms=5000)
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        assert await breaker.can_execute() is True

    async def test_failures_open_circuit_at_threshold(self, fake_clock: FakeClock) -> None:
        observer = TrackingObserver()
        breaker = CircuitBreaker(
            clock=fake_clock,
            observer=observer,
            failure_threshold=3,
            cooldown_ms=5000,
        )

        # 1st failure
        await breaker.record_failure()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 1

        # 2nd failure
        await breaker.record_failure()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 2

        # 3rd failure reaches threshold
        await breaker.record_failure()
        assert breaker.state == BreakerState.OPEN
        assert breaker.consecutive_failures == 3
        assert observer.breaker_states == ["open"]

        # Fast-fail while OPEN
        assert await breaker.can_execute() is False

    async def test_fast_fail_in_open_state(self, fake_clock: FakeClock) -> None:
        breaker = CircuitBreaker(clock=fake_clock, failure_threshold=2, cooldown_ms=5000)
        await breaker.record_failure()
        await breaker.record_failure()
        assert breaker.state == BreakerState.OPEN

        # Immediate execute should raise StorageUnavailable without calling func
        called = False

        async def dummy_call() -> str:
            nonlocal called
            called = True
            return "ok"

        with pytest.raises(StorageUnavailable, match="circuit breaker is OPEN"):
            await breaker.execute(dummy_call)

        assert not called

    async def test_cooldown_transitions_to_half_open(self, fake_clock: FakeClock) -> None:
        observer = TrackingObserver()
        breaker = CircuitBreaker(
            clock=fake_clock,
            observer=observer,
            failure_threshold=2,
            cooldown_ms=5000,
        )
        await breaker.record_failure()
        await breaker.record_failure()
        assert breaker.state == BreakerState.OPEN

        # Advance clock by 4000ms (still within cooldown)
        fake_clock.advance(4000)
        assert await breaker.can_execute() is False
        assert breaker.state == BreakerState.OPEN

        # Advance past cooldown (1001ms more = 5001ms total)
        fake_clock.advance(1001)
        assert await breaker.can_execute() is True
        assert breaker.state == BreakerState.HALF_OPEN
        assert observer.breaker_states == ["open", "half_open"]

    async def test_successful_probe_closes_circuit(self, fake_clock: FakeClock) -> None:
        observer = TrackingObserver()
        breaker = CircuitBreaker(
            clock=fake_clock,
            observer=observer,
            failure_threshold=2,
            cooldown_ms=5000,
        )
        await breaker.record_failure()
        await breaker.record_failure()

        # Advance past cooldown to HALF_OPEN
        fake_clock.advance(5000)
        assert await breaker.can_execute() is True
        assert breaker.state == BreakerState.HALF_OPEN

        # Successful execution
        async def successful_call() -> str:
            return "success"

        result = await breaker.execute(successful_call)
        assert result == "success"
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        assert observer.breaker_states == ["open", "half_open", "closed"]

    async def test_failed_probe_reopens_circuit(self, fake_clock: FakeClock) -> None:
        observer = TrackingObserver()
        breaker = CircuitBreaker(
            clock=fake_clock,
            observer=observer,
            failure_threshold=2,
            cooldown_ms=5000,
        )
        await breaker.record_failure()
        await breaker.record_failure()

        # Advance past cooldown to HALF_OPEN
        fake_clock.advance(5000)
        assert await breaker.can_execute() is True
        assert breaker.state == BreakerState.HALF_OPEN

        # Probe fails
        async def failing_call() -> None:
            raise StorageUnavailable("Probe failed")

        with pytest.raises(StorageUnavailable):
            await breaker.execute(failing_call)

        # Must trip immediately back to OPEN
        assert breaker.state == BreakerState.OPEN
        assert observer.breaker_states == ["open", "half_open", "open"]

    async def test_manual_reset(self, fake_clock: FakeClock) -> None:
        breaker = CircuitBreaker(clock=fake_clock, failure_threshold=1)
        await breaker.record_failure()
        assert breaker.state == BreakerState.OPEN

        breaker.reset()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        assert await breaker.can_execute() is True


class TestCircuitBreakerStorage:
    """Test CircuitBreakerStorage wrapper behavior."""

    async def test_storage_operations_guarded_by_breaker(
        self,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        breaker = CircuitBreaker(clock=fake_clock, failure_threshold=2, cooldown_ms=3000)
        cb_storage = CircuitBreakerStorage(memory_store, breaker)

        # Successful calls pass through
        count = await cb_storage.incr_expiring("key:test", 1, 10000)
        assert count == 1
        assert breaker.state == BreakerState.CLOSED

        # Failures trip the breaker
        await breaker.record_failure()
        await breaker.record_failure()
        assert breaker.state == BreakerState.OPEN

        # Subsequent storage operations immediately fail with StorageUnavailable
        with pytest.raises(StorageUnavailable, match="circuit breaker is OPEN"):
            await cb_storage.incr_expiring("key:test", 1, 10000)

        with pytest.raises(StorageUnavailable, match="circuit breaker is OPEN"):
            await cb_storage.now_ms()

    async def test_ping_respects_circuit_breaker(
        self,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        breaker = CircuitBreaker(clock=fake_clock, failure_threshold=1)
        cb_storage = CircuitBreakerStorage(memory_store, breaker)

        assert await cb_storage.ping() is True

        await breaker.record_failure()
        assert breaker.state == BreakerState.OPEN
        assert await cb_storage.ping() is False
