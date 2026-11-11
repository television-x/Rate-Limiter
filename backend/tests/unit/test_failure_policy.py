"""Unit tests for failure policy resolution and ResilientLimiter.

Per AGENTS.md §Phase 7:
- Policy.on_failure:
  - open: allow, degraded=true, reason=DEGRADED_ALLOWED
  - closed: 503 with Retry-After (raises StorageUnavailable)
  - local_fallback: per-instance memory limiter, explicitly not global, degraded=true
- Policy.on_contention:
  - reject: raises ContentionExhausted
  - allow: allows with degraded=true
- Uses fault-injecting storage wrapper
"""

from __future__ import annotations

import pytest

from app.core.algorithms.fixed_window import FixedWindowLimiter
from app.core.clock import FakeClock
from app.core.errors import ContentionExhausted, StorageUnavailable
from app.core.failure_policy import (
    ResilientLimiter,
    resolve_contention_failure,
    resolve_storage_failure,
)
from app.core.models import (
    Algorithm,
    ContentionMode,
    DecisionReason,
    FailureMode,
    Policy,
)
from app.core.storage.base import Storage, Transaction
from app.core.storage.memory import MemoryStorage

pytestmark = pytest.mark.unit


class FaultInjectingStorage(Storage):
    """Storage wrapper that injects StorageUnavailable failures on demand."""

    def __init__(self, inner: Storage, should_fail: bool = False) -> None:
        self.inner = inner
        self.should_fail = should_fail
        self.call_count = 0

    def _check_fault(self) -> None:
        self.call_count += 1
        if self.should_fail:
            raise StorageUnavailable("Injected storage failure")

    async def now_ms(self) -> int:
        self._check_fault()
        return await self.inner.now_ms()

    async def incr_expiring(self, key: str, by: int, ttl_ms: int) -> int:
        self._check_fault()
        return await self.inner.incr_expiring(key, by, ttl_ms)

    async def incr_expiring_peek(
        self, key: str, by: int, ttl_ms: int, peek_key: str
    ) -> tuple[int, int]:
        self._check_fault()
        return await self.inner.incr_expiring_peek(key, by, ttl_ms, peek_key)

    async def decr(self, key: str, by: int) -> int:
        self._check_fault()
        return await self.inner.decr(key, by)

    async def optimistic(self, watch_keys: list[str]) -> Transaction:
        self._check_fault()
        return await self.inner.optimistic(watch_keys)

    async def delete(self, key: str) -> bool:
        self._check_fault()
        return await self.inner.delete(key)

    async def ping(self) -> bool:
        if self.should_fail:
            return False
        return await self.inner.ping()

    async def close(self) -> None:
        await self.inner.close()


class TestPolicyResolutionFunctions:
    """Test standalone policy resolution helpers."""

    def test_resolve_storage_open_mode(self) -> None:
        policy = Policy(
            id="test-open",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=10,
            window_ms=60000,
            on_failure=FailureMode.OPEN,
        )
        decision = resolve_storage_failure(policy)
        assert decision.allowed is True
        assert decision.degraded is True
        assert decision.reason == DecisionReason.DEGRADED_ALLOWED
        assert decision.remaining is None

    def test_resolve_storage_closed_mode(self) -> None:
        policy = Policy(
            id="test-closed",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=10,
            window_ms=60000,
            on_failure=FailureMode.CLOSED,
        )
        with pytest.raises(StorageUnavailable, match="on_failure=closed"):
            resolve_storage_failure(policy)

    def test_resolve_contention_reject_mode(self) -> None:
        policy = Policy(
            id="test-contention-reject",
            algorithm=Algorithm.TOKEN_BUCKET,
            capacity=10,
            refill_per_sec=2.0,
            on_contention=ContentionMode.REJECT,
        )
        with pytest.raises(ContentionExhausted):
            resolve_contention_failure(policy, key="some-key")

    def test_resolve_contention_allow_mode(self) -> None:
        policy = Policy(
            id="test-contention-allow",
            algorithm=Algorithm.TOKEN_BUCKET,
            capacity=10,
            refill_per_sec=2.0,
            on_contention=ContentionMode.ALLOW,
        )
        decision = resolve_contention_failure(policy, key="some-key")
        assert decision.allowed is True
        assert decision.degraded is True
        assert decision.reason == DecisionReason.DEGRADED_ALLOWED


class TestResilientLimiterIntegration:
    """Test ResilientLimiter with FaultInjectingStorage and algorithms."""

    async def test_normal_operation_when_storage_healthy(
        self,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        policy = Policy(
            id="test-fw",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=5,
            window_ms=10000,
            on_failure=FailureMode.OPEN,
        )
        faulty_store = FaultInjectingStorage(memory_store, should_fail=False)
        primary = FixedWindowLimiter(policy, faulty_store)
        resilient = ResilientLimiter(primary_limiter=primary)

        decision = await resilient.check("key:healthy", cost=1)
        assert decision.allowed is True
        assert decision.degraded is False
        assert decision.remaining == 4

    async def test_fails_open_when_storage_fails(
        self,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        policy = Policy(
            id="test-open",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=5,
            window_ms=10000,
            on_failure=FailureMode.OPEN,
        )
        faulty_store = FaultInjectingStorage(memory_store, should_fail=True)
        primary = FixedWindowLimiter(policy, faulty_store)
        resilient = ResilientLimiter(primary_limiter=primary)

        decision = await resilient.check("key:fail-open", cost=1)
        assert decision.allowed is True
        assert decision.degraded is True
        assert decision.reason == DecisionReason.DEGRADED_ALLOWED

    async def test_fails_closed_when_storage_fails(
        self,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        policy = Policy(
            id="test-closed",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=5,
            window_ms=10000,
            on_failure=FailureMode.CLOSED,
        )
        faulty_store = FaultInjectingStorage(memory_store, should_fail=True)
        primary = FixedWindowLimiter(policy, faulty_store)
        resilient = ResilientLimiter(primary_limiter=primary)

        with pytest.raises(StorageUnavailable):
            await resilient.check("key:fail-closed", cost=1)

    async def test_local_fallback_enforces_limit_locally(
        self,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        policy = Policy(
            id="test-local-fallback",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=3,
            window_ms=10000,
            on_failure=FailureMode.LOCAL_FALLBACK,
        )
        # Primary storage is failing
        faulty_primary = FaultInjectingStorage(memory_store, should_fail=True)
        primary = FixedWindowLimiter(policy, faulty_primary)

        # Local fallback storage is an independent MemoryStorage
        local_store = MemoryStorage(clock=fake_clock)
        fallback = FixedWindowLimiter(policy, local_store)

        resilient = ResilientLimiter(primary_limiter=primary, fallback_limiter=fallback)

        # 1st request to local fallback
        d1 = await resilient.check("client:alice", cost=1)
        assert d1.allowed is True
        assert d1.degraded is True
        assert d1.remaining == 2
        assert d1.reason == DecisionReason.DEGRADED_ALLOWED

        # 2nd request
        d2 = await resilient.check("client:alice", cost=1)
        assert d2.allowed is True
        assert d2.degraded is True
        assert d2.remaining == 1

        # 3rd request (quota exhausted)
        d3 = await resilient.check("client:alice", cost=1)
        assert d3.allowed is True
        assert d3.degraded is True
        assert d3.remaining == 0

        # 4th request must be denied locally by the in-memory limiter
        d4 = await resilient.check("client:alice", cost=1)
        assert d4.allowed is False
        assert d4.degraded is True
        assert d4.reason == DecisionReason.LIMIT_EXCEEDED
