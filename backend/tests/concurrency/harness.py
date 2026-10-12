"""Concurrency test harness.

Provides runners to dispatch N simultaneous rate-limit checks against a
limiter using asyncio.gather synchronized via an asyncio.Barrier.
"""

from __future__ import annotations

import asyncio

from app.core.limiter import Limiter
from app.core.models import Decision


async def run_concurrent_requests(
    limiter: Limiter,
    key: str,
    count: int,
    cost: int = 1,
) -> list[Decision]:
    """Execute `count` rate-limit checks simultaneously against a single key.

    Uses an asyncio.Barrier so all N worker coroutines synchronize before
    firing their check() calls at the exact same moment.

    Args:
        limiter: The Limiter instance to test.
        key: The rate-limit storage key.
        count: Number of concurrent tasks.
        cost: Cost per check.

    Returns:
        List of all Decision objects returned by the limiter.
    """
    barrier = asyncio.Barrier(count)

    async def worker() -> Decision:
        # Wait for all workers to reach the starting gate
        await barrier.wait()
        return await limiter.check(key, cost=cost)

    tasks = [asyncio.create_task(worker()) for _ in range(count)]
    results: list[Decision] = await asyncio.gather(*tasks)
    return results
