"""Concurrency test suite for rate limiting algorithms.

Tests race conditions under high concurrent contention using the concurrency harness.
Per AGENTS.md Phase 4:
- Fixed Window: N=200 concurrent tasks at L=50 gives EXACTLY 50 allowed on a single key.
- Sliding Window Counter: N=200 at L=50 gives <= L allowed.
- Fixed Window invariant verified across 20 repeated runs against Redis.
"""

from __future__ import annotations

import pytest
from tests.concurrency.harness import run_concurrent_requests

from app.core.algorithms.fixed_window import FixedWindowLimiter
from app.core.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from app.core.models import Algorithm, Policy
from app.core.storage.memory import MemoryStorage
from app.core.storage.redis_store import RedisStorage


@pytest.mark.concurrency
class TestFixedWindowConcurrency:
    """Concurrency verification for Fixed Window."""

    async def test_fixed_window_memory_exact_limit(self, memory_store: MemoryStorage) -> None:
        """Memory store: N=200 concurrent tasks at L=50 gives exactly 50 allowed."""
        policy = Policy(
            id="p-fw-mem-conc",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=50,
            window_ms=60_000,
        )
        limiter = FixedWindowLimiter(policy, memory_store)
        key = "rl:v1:{fw|user:conc_mem}:fixed_window"

        results = await run_concurrent_requests(limiter, key, count=200)

        allowed = sum(1 for d in results if d.allowed)
        denied = sum(1 for d in results if not d.allowed)

        assert allowed == 50
        assert denied == 150

    async def test_fixed_window_redis_exact_limit(self, redis_store: RedisStorage) -> None:
        """Redis store: N=200 concurrent tasks at L=50 gives exactly 50 allowed."""
        policy = Policy(
            id="p-fw-redis-conc",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=50,
            window_ms=60_000,
        )
        limiter = FixedWindowLimiter(policy, redis_store)
        key = "rl:v1:{fw|user:conc_redis}:fixed_window"

        results = await run_concurrent_requests(limiter, key, count=200)

        allowed = sum(1 for d in results if d.allowed)
        denied = sum(1 for d in results if not d.allowed)

        assert allowed == 50
        assert denied == 150

    async def test_fixed_window_redis_repeated_invariant_20_runs(
        self, redis_store: RedisStorage
    ) -> None:
        """Verify the exact-50 invariant holds across 20 repeated runs on Redis."""
        policy = Policy(
            id="p-fw-redis-rep",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=50,
            window_ms=60_000,
        )

        for iteration in range(20):
            limiter = FixedWindowLimiter(policy, redis_store)
            key = f"rl:v1:{{fw|user:run_{iteration}}}:fixed_window"

            results = await run_concurrent_requests(limiter, key, count=200)

            allowed = sum(1 for d in results if d.allowed)
            denied = sum(1 for d in results if not d.allowed)

            assert allowed == 50, (
                f"Iteration {iteration}: expected exactly 50 allowed, got {allowed}"
            )
            assert denied == 150


@pytest.mark.concurrency
class TestSlidingCounterConcurrency:
    """Concurrency verification for Sliding Window Counter."""

    async def test_sliding_counter_memory_bounded_limit(self, memory_store: MemoryStorage) -> None:
        """Memory store: N=200 concurrent tasks at L=50 gives <= 50 allowed."""
        policy = Policy(
            id="p-sc-mem-conc",
            algorithm=Algorithm.SLIDING_WINDOW_COUNTER,
            limit=50,
            window_ms=60_000,
        )
        limiter = SlidingWindowCounterLimiter(policy, memory_store)
        key = "rl:v1:{sc|user:conc_mem}:sliding_counter"

        results = await run_concurrent_requests(limiter, key, count=200)

        allowed = sum(1 for d in results if d.allowed)
        assert allowed <= 50, f"Expected <= 50 allowed, got {allowed}"

    async def test_sliding_counter_redis_bounded_limit(self, redis_store: RedisStorage) -> None:
        """Redis store: N=200 concurrent tasks at L=50 gives <= 50 allowed."""
        policy = Policy(
            id="p-sc-redis-conc",
            algorithm=Algorithm.SLIDING_WINDOW_COUNTER,
            limit=50,
            window_ms=60_000,
        )
        limiter = SlidingWindowCounterLimiter(policy, redis_store)
        key = "rl:v1:{sc|user:conc_redis}:sliding_counter"

        results = await run_concurrent_requests(limiter, key, count=200)

        allowed = sum(1 for d in results if d.allowed)
        assert allowed <= 50, f"Expected <= 50 allowed, got {allowed}"
