"""Unit tests for the Sliding Window Log rate-limiting algorithm.

Tests per AGENTS.md Phase 3:
- Exact limit enforcement within sliding window
- Same-millisecond uniqueness (UUID suffix prevents collision)
- Old entries slide out and free quota incrementally
- Denied requests do not write entries
- Accurate retry_after_ms calculation based on oldest entry
- cost > 1 behavior
- Observer events emitted
"""

from __future__ import annotations

import pytest

from app.core.algorithms.sliding_window_log import SlidingWindowLogLimiter
from app.core.clock import FakeClock
from app.core.models import (
    Accuracy,
    Algorithm,
    DecisionReason,
    Policy,
)
from app.core.observer import Observer
from app.core.storage.memory import MemoryStorage


@pytest.fixture
def sliding_log_policy() -> Policy:
    """Standard sliding window log policy: limit 5 per 10_000 ms."""
    return Policy(
        id="test-sliding-log",
        algorithm=Algorithm.SLIDING_WINDOW_LOG,
        limit=5,
        window_ms=10_000,
        cost_default=1,
    )


@pytest.mark.unit
class TestSlidingWindowLogLimiter:
    """Test suite for SlidingWindowLogLimiter."""

    async def test_allows_up_to_limit(
        self,
        sliding_log_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Allows exactly limit requests within the window."""
        limiter = SlidingWindowLogLimiter(sliding_log_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_log"

        for i in range(5):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True
            assert d.remaining == 4 - i
            assert d.accuracy == Accuracy.EXACT

        # 6th request rejected
        d_rejected = await limiter.check(key, cost=1)
        assert d_rejected.allowed is False
        assert d_rejected.remaining == 0
        assert d_rejected.reason == DecisionReason.LIMIT_EXCEEDED
        assert d_rejected.retry_after_ms is not None
        assert d_rejected.retry_after_ms > 0

    async def test_same_millisecond_requests_are_all_counted(
        self,
        sliding_log_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Multiple requests in the exact same millisecond must not overwrite each other."""
        limiter = SlidingWindowLogLimiter(sliding_log_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_log"

        # Do not advance clock: all happen at the exact same ms
        for _ in range(5):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True

        # 6th request at the same ms is denied
        d = await limiter.check(key, cost=1)
        assert d.allowed is False

    async def test_entries_expire_individually_on_sliding_basis(
        self,
        sliding_log_policy: Policy,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        """Unlike fixed window, entries expire individually as time slides forward."""
        limiter = SlidingWindowLogLimiter(sliding_log_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_log"

        # T=0: 2 requests
        await limiter.check(key, cost=1)
        await limiter.check(key, cost=1)

        # T=4000: 3 requests (bucket now full at 5)
        fake_clock.advance(4000)
        await limiter.check(key, cost=1)
        await limiter.check(key, cost=1)
        await limiter.check(key, cost=1)

        # T=4000: 6th request rejected
        d = await limiter.check(key, cost=1)
        assert d.allowed is False

        # T=10001: The first two requests (from T=0) have slid out (>10_000ms old)
        fake_clock.advance(6001)  # now T=10001
        d1 = await limiter.check(key, cost=1)
        assert d1.allowed is True
        assert d1.remaining == 1

        d2 = await limiter.check(key, cost=1)
        assert d2.allowed is True
        assert d2.remaining == 0

        # Now full again (3 from T=4000 + 2 from T=10001 = 5)
        d3 = await limiter.check(key, cost=1)
        assert d3.allowed is False

    async def test_denied_requests_do_not_record_entries(
        self,
        sliding_log_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Denied requests must not add entries to the sorted set."""
        limiter = SlidingWindowLogLimiter(sliding_log_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_log"

        for _ in range(5):
            await limiter.check(key, cost=1)

        # Send 10 denied requests
        for _ in range(10):
            d = await limiter.check(key, cost=1)
            assert d.allowed is False

        # Verify sorted set has exactly 5 members
        entry = memory_store._get_live(key)
        assert entry is not None
        assert len(entry.value) == 5

    async def test_cost_greater_than_one(
        self,
        sliding_log_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Single check can consume multiple log slots."""
        limiter = SlidingWindowLogLimiter(sliding_log_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_log"

        d = await limiter.check(key, cost=3)
        assert d.allowed is True
        assert d.remaining == 2

        d2 = await limiter.check(key, cost=3)
        assert d2.allowed is False  # Only 2 left, cost=3 denied

        d3 = await limiter.check(key, cost=2)
        assert d3.allowed is True
        assert d3.remaining == 0

    async def test_observer_events(
        self,
        sliding_log_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Decision events are emitted to observer."""
        called: list[bool] = []

        class TestObserver(Observer):
            def on_decision(self, policy_id: str, decision: object) -> None:
                called.append(decision.allowed)

        limiter = SlidingWindowLogLimiter(sliding_log_policy, memory_store, observer=TestObserver())
        key = "rl:v1:{test|user:1}:sliding_log"

        await limiter.check(key, cost=1)
        assert called == [True]
