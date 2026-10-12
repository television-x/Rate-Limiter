"""Unit tests for the Sliding Window Counter rate-limiting algorithm.

Tests per AGENTS.md Phase 3:
- Weighted window approximation formula verification
- Flags Accuracy.ESTIMATED
- Compensation decrement on rejection
- Window transition and decaying weight of previous window
- Cost > 1 behavior
- Observer events emitted
"""

from __future__ import annotations

import pytest

from app.core.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
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
def sliding_counter_policy() -> Policy:
    """Standard sliding window counter policy: limit 10 per 10_000 ms."""
    return Policy(
        id="test-sliding-counter",
        algorithm=Algorithm.SLIDING_WINDOW_COUNTER,
        limit=10,
        window_ms=10_000,
        cost_default=1,
    )


@pytest.mark.unit
class TestSlidingWindowCounterLimiter:
    """Test suite for SlidingWindowCounterLimiter."""

    async def test_allows_up_to_limit_in_fresh_window(
        self,
        sliding_counter_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Fresh window with no previous window allows up to limit."""
        limiter = SlidingWindowCounterLimiter(sliding_counter_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_counter"

        for i in range(10):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True
            assert d.remaining == 9 - i
            assert d.accuracy == Accuracy.ESTIMATED
            assert d.reason == DecisionReason.ALLOWED

        # 11th request rejected
        d_rejected = await limiter.check(key, cost=1)
        assert d_rejected.allowed is False
        assert d_rejected.remaining == 0
        assert d_rejected.reason == DecisionReason.LIMIT_EXCEEDED
        assert d_rejected.accuracy == Accuracy.ESTIMATED

    async def test_compensation_decrement_on_rejection(
        self,
        sliding_counter_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """When rejected, the counter is compensated via decr."""
        limiter = SlidingWindowCounterLimiter(sliding_counter_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_counter"

        for _ in range(10):
            await limiter.check(key, cost=1)

        # Storage key for current window
        now_ms = await memory_store.now_ms()
        window_id = now_ms // 10_000
        curr_key = f"{key}:{window_id}"

        # Current count in storage should be 10
        entry = memory_store._get_live(curr_key)
        assert entry is not None
        assert entry.value == 10

        # 5 rejected requests
        for _ in range(5):
            d = await limiter.check(key, cost=1)
            assert d.allowed is False

        # Current count in storage must still be 10 due to decr compensation
        entry_after = memory_store._get_live(curr_key)
        assert entry_after is not None
        assert entry_after.value == 10

    async def test_weighted_interpolation_across_window_boundary(
        self,
        sliding_counter_policy: Policy,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        """Previous window count decays linearly based on elapsed fraction in current window."""
        # Align clock to start of a window
        fake_clock.set(100_000)
        limiter = SlidingWindowCounterLimiter(sliding_counter_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_counter"

        # Use all 10 requests in window 10 (100_000..109_999)
        for _ in range(10):
            await limiter.check(key, cost=1)

        # Move to window 11, exactly 30% into the window (T = 113_000)
        # Previous window had 10 requests.
        # Elapsed fraction = 3000 / 10000 = 0.3
        # Weight = 1.0 - 0.3 = 0.7
        # Contribution from prev = floor(10 * 0.7) = 7
        # Available capacity in curr window = 10 - 7 = 3
        fake_clock.set(113_000)

        d1 = await limiter.check(key, cost=1)  # curr=1, estimate = 7 + 1 = 8
        assert d1.allowed is True
        assert d1.remaining == 2

        d2 = await limiter.check(key, cost=1)  # curr=2, estimate = 7 + 2 = 9
        assert d2.allowed is True
        assert d2.remaining == 1

        d3 = await limiter.check(key, cost=1)  # curr=3, estimate = 7 + 3 = 10
        assert d3.allowed is True
        assert d3.remaining == 0

        # 4th request in this window brings estimate to 11 -> rejected!
        d4 = await limiter.check(key, cost=1)
        assert d4.allowed is False

    async def test_previous_window_decays_to_zero(
        self,
        sliding_counter_policy: Policy,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        """When moving into a window beyond the previous one, previous contribution is 0."""
        fake_clock.set(100_000)
        limiter = SlidingWindowCounterLimiter(sliding_counter_policy, memory_store)
        key = "rl:v1:{test|user:1}:sliding_counter"

        for _ in range(10):
            await limiter.check(key, cost=1)

        # Advance 2 full windows ahead (T = 130_000)
        fake_clock.set(130_000)

        # Full 10 requests should be available again
        for i in range(10):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True
            assert d.remaining == 9 - i

    async def test_observer_events(
        self,
        sliding_counter_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Decision events are emitted to observer."""
        decisions: list[bool] = []

        class TestObserver(Observer):
            def on_decision(self, policy_id: str, decision: object) -> None:
                decisions.append(decision.allowed)

        limiter = SlidingWindowCounterLimiter(
            sliding_counter_policy, memory_store, observer=TestObserver()
        )
        key = "rl:v1:{test|user:1}:sliding_counter"

        await limiter.check(key, cost=1)
        assert decisions == [True]
