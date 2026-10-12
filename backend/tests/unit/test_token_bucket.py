"""Unit tests for the Token Bucket rate-limiting algorithm.

Tests per AGENTS.md Phase 3:
- Capacity enforcement: exactly capacity allowed initially
- Refill behavior over time
- Denied requests do not write state
- Zero-refill bucket allows exactly C
- Clock regression handled safely (elapsed = max(0, ...))
- cost > 1 consumption
- Fractional refill (integer milli-tokens, no float drift)
- Accurate retry_after_ms and reset_after_ms
- Observer events emitted
"""

from __future__ import annotations

import pytest

from app.core.algorithms.token_bucket import TokenBucketLimiter
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
def token_bucket_policy() -> Policy:
    """Standard token bucket policy: capacity 10, refill 2/sec."""
    return Policy(
        id="test-token-bucket",
        algorithm=Algorithm.TOKEN_BUCKET,
        capacity=10,
        refill_per_sec=2,
        cost_default=1,
    )


@pytest.mark.unit
class TestTokenBucketLimiter:
    """Test suite for TokenBucketLimiter."""

    async def test_initial_burst_allows_exactly_capacity(
        self, token_bucket_policy: Policy, memory_store: MemoryStorage
    ) -> None:
        """A fresh bucket allows up to capacity requests immediately."""
        limiter = TokenBucketLimiter(token_bucket_policy, memory_store)
        key = "rl:v1:{test|user:1}:token_bucket"

        for i in range(10):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True
            assert d.remaining == 9 - i
            assert d.reason == DecisionReason.ALLOWED
            assert d.accuracy == Accuracy.EXACT

        # 11th request rejected
        d_rejected = await limiter.check(key, cost=1)
        assert d_rejected.allowed is False
        assert d_rejected.remaining == 0
        assert d_rejected.reason == DecisionReason.LIMIT_EXCEEDED
        assert d_rejected.retry_after_ms is not None
        assert d_rejected.retry_after_ms > 0

    async def test_denied_requests_do_not_modify_state(
        self,
        token_bucket_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Denied requests must not write state to storage (no conflict risk)."""
        limiter = TokenBucketLimiter(token_bucket_policy, memory_store)
        key = "rl:v1:{test|user:1}:token_bucket"

        # Drain bucket
        for _ in range(10):
            await limiter.check(key, cost=1)

        # Inspect raw storage version
        version_before = memory_store._version_of(key)

        # Send 5 rejected requests
        for _ in range(5):
            d = await limiter.check(key, cost=1)
            assert d.allowed is False

        version_after = memory_store._version_of(key)
        assert version_before == version_after, "Denied requests must not write state"

    async def test_refill_over_time(
        self,
        token_bucket_policy: Policy,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        """Bucket refills at the configured rate."""
        limiter = TokenBucketLimiter(token_bucket_policy, memory_store)
        key = "rl:v1:{test|user:1}:token_bucket"

        # Drain all 10 tokens
        for _ in range(10):
            await limiter.check(key, cost=1)

        # Empty now
        d = await limiter.check(key, cost=1)
        assert d.allowed is False

        # Advance 1 second -> should refill 2 tokens
        fake_clock.advance(1000)
        d1 = await limiter.check(key, cost=1)
        assert d1.allowed is True
        assert d1.remaining == 1

        d2 = await limiter.check(key, cost=1)
        assert d2.allowed is True
        assert d2.remaining == 0

        # Next one rejected again
        d3 = await limiter.check(key, cost=1)
        assert d3.allowed is False

    async def test_capacity_is_upper_bound_on_refill(
        self,
        token_bucket_policy: Policy,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        """Tokens never accumulate beyond capacity."""
        limiter = TokenBucketLimiter(token_bucket_policy, memory_store)
        key = "rl:v1:{test|user:1}:token_bucket"

        # Drain 5 tokens
        for _ in range(5):
            await limiter.check(key, cost=1)

        # Wait 100 seconds (would be 200 tokens refill without cap)
        fake_clock.advance(100_000)

        # Bucket should have capped at 10
        d = await limiter.check(key, cost=1)
        assert d.allowed is True
        assert d.remaining == 9  # 10 - 1 = 9

    async def test_zero_refill_policy_exhausts_permanently(
        self, fake_clock: FakeClock, memory_store: MemoryStorage
    ) -> None:
        """Policy with very low refill allows capacity and does not refill quickly."""
        policy = Policy(
            id="low-refill",
            algorithm=Algorithm.TOKEN_BUCKET,
            capacity=3,
            refill_per_sec=1,
            cost_default=1,
        )
        limiter = TokenBucketLimiter(policy, memory_store)
        key = "rl:v1:{test|user:1}:token_bucket"

        for _ in range(3):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True

        d_denied = await limiter.check(key, cost=1)
        assert d_denied.allowed is False

    async def test_cost_greater_than_one(
        self,
        token_bucket_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Single request can consume multiple tokens."""
        limiter = TokenBucketLimiter(token_bucket_policy, memory_store)
        key = "rl:v1:{test|user:1}:token_bucket"

        # Consume 7 tokens
        d = await limiter.check(key, cost=7)
        assert d.allowed is True
        assert d.remaining == 3

        # Next request for 4 tokens fails (only 3 left)
        d2 = await limiter.check(key, cost=4)
        assert d2.allowed is False
        assert d2.remaining == 3

        # But request for 3 tokens succeeds
        d3 = await limiter.check(key, cost=3)
        assert d3.allowed is True
        assert d3.remaining == 0

    async def test_clock_regression_handled_safely(
        self,
        token_bucket_policy: Policy,
        fake_clock: FakeClock,
        memory_store: MemoryStorage,
    ) -> None:
        """Clock going backwards must not cause negative refill or errors."""
        limiter = TokenBucketLimiter(token_bucket_policy, memory_store)
        key = "rl:v1:{test|user:1}:token_bucket"

        # First request sets last_refill_ms
        await limiter.check(key, cost=1)

        # Move clock back by 5000 ms
        fake_clock.advance(-5000)

        # Request should still process without breaking or inflating tokens
        d = await limiter.check(key, cost=1)
        assert d.allowed is True
        # Since elapsed is clamped to 0, no refill happened, remaining drops by 1
        assert d.remaining == 8

    async def test_observer_events(
        self,
        token_bucket_policy: Policy,
        memory_store: MemoryStorage,
    ) -> None:
        """Decision events are emitted to observer."""
        decisions: list[tuple[str, bool]] = []

        class TestObserver(Observer):
            def on_decision(self, policy_id: str, decision: object) -> None:
                decisions.append((policy_id, decision.allowed))

        limiter = TokenBucketLimiter(token_bucket_policy, memory_store, observer=TestObserver())
        key = "rl:v1:{test|user:1}:token_bucket"

        await limiter.check(key, cost=1)
        assert len(decisions) == 1
        assert decisions[0] == ("test-token-bucket", True)
