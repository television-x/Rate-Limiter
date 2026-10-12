"""Integration tests for RedisStorage backend and single-atomic-op limiters.

Tests Redis TIME, TTL presence (PTTL), atomic increment + peek, and
algorithm execution against a real Redis 7 instance.
"""

from __future__ import annotations

import time

import pytest

from app.core.algorithms.fixed_window import FixedWindowLimiter
from app.core.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from app.core.models import Algorithm, DecisionReason, Policy
from app.core.storage.redis_store import RedisStorage


@pytest.mark.integration
class TestRedisStorageIntegration:
    """Integration tests directly verifying Redis storage primitives."""

    async def test_incr_expiring_always_sets_pttl(self, redis_store: RedisStorage) -> None:
        """Verify that incr_expiring unconditionally sets a valid TTL in milliseconds."""
        key = "test:ttl:key"
        ttl_ms = 5000

        # Increment key
        val = await redis_store.incr_expiring(key, by=1, ttl_ms=ttl_ms)
        assert val == 1

        # Check PTTL via Redis PTTL command
        pttl = await redis_store.pttl(key)
        assert pttl > 0, "Key must have an active TTL set"
        assert pttl <= ttl_ms, "PTTL must not exceed requested ttl_ms"

        # Subsequent increment refreshes TTL
        await redis_store.incr_expiring(key, by=2, ttl_ms=ttl_ms)
        pttl2 = await redis_store.pttl(key)
        assert pttl2 > 0
        assert pttl2 <= ttl_ms

    async def test_now_ms_syncs_with_system_clock(self, redis_store: RedisStorage) -> None:
        """Redis TIME must agree with local system epoch time within a small tolerance."""
        sys_now = int(time.time() * 1000)
        redis_now = await redis_store.now_ms()

        # Should match within 2 seconds
        delta = abs(redis_now - sys_now)
        assert delta < 2000, f"Clock drift too large: delta={delta}ms"

    async def test_incr_expiring_peek_atomicity(self, redis_store: RedisStorage) -> None:
        """Verify atomic increment + peek on Redis."""
        curr_key = "test:peek:curr"
        prev_key = "test:peek:prev"

        # Setup prev key with 42
        await redis_store.incr_expiring(prev_key, by=42, ttl_ms=10000)

        # Increment curr and peek prev
        curr_val, prev_val = await redis_store.incr_expiring_peek(
            curr_key, by=3, ttl_ms=5000, peek_key=prev_key
        )
        assert curr_val == 3
        assert prev_val == 42

        # PTTL on curr_key must be active
        assert await redis_store.pttl(curr_key) > 0


@pytest.mark.integration
class TestLimitersOnRedis:
    """Verify Fixed Window and Sliding Window Counter against real Redis."""

    async def test_fixed_window_on_redis(self, redis_store: RedisStorage) -> None:
        """Fixed Window limits correctly against Redis."""
        policy = Policy(
            id="p-fw-redis",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=5,
            window_ms=10_000,
            cost_default=1,
        )
        limiter = FixedWindowLimiter(policy, redis_store)
        key = "rl:v1:{fw|user:redis_user}:fixed_window"

        for i in range(5):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True
            assert d.remaining == 4 - i
            assert d.reason == DecisionReason.ALLOWED

        # 6th request rejected
        d_rejected = await limiter.check(key, cost=1)
        assert d_rejected.allowed is False
        assert d_rejected.remaining == 0
        assert d_rejected.reason == DecisionReason.LIMIT_EXCEEDED

    async def test_sliding_counter_on_redis(self, redis_store: RedisStorage) -> None:
        """Sliding Window Counter limits correctly and compensates on reject against Redis."""
        policy = Policy(
            id="p-sc-redis",
            algorithm=Algorithm.SLIDING_WINDOW_COUNTER,
            limit=5,
            window_ms=10_000,
            cost_default=1,
        )
        limiter = SlidingWindowCounterLimiter(policy, redis_store)
        key = "rl:v1:{sc|user:redis_user}:sliding_counter"

        for i in range(5):
            d = await limiter.check(key, cost=1)
            assert d.allowed is True
            assert d.remaining == 4 - i

        # 6th request rejected
        d_rejected = await limiter.check(key, cost=1)
        assert d_rejected.allowed is False
        assert d_rejected.remaining == 0
