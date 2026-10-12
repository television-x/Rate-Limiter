"""Shared test fixtures for the rate limiter test suite.

Provides FakeClock and store factory fixtures used across unit,
contract, and integration tests.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from app.core.clock import FakeClock
from app.core.storage.memory import MemoryStorage


@pytest.fixture
def fake_clock() -> FakeClock:
    """Provide a deterministic FakeClock starting at a realistic epoch-ms."""
    return FakeClock(start_ms=1_000_000_000_000)


@pytest.fixture
async def memory_store(fake_clock: FakeClock) -> AsyncGenerator[MemoryStorage, None]:
    """Provide a fresh MemoryStorage instance backed by a FakeClock."""
    store = MemoryStorage(clock=fake_clock)
    yield store
    await store.close()


@pytest.fixture
async def redis_store() -> AsyncGenerator[object, None]:
    """Provide a clean RedisStorage instance connected to test Redis."""
    from app.core.storage.redis_store import RedisStorage

    test_url = "redis://127.0.0.1:6379/15"
    store = RedisStorage(url=test_url)
    try:
        await store.client.ping()
    except Exception as exc:
        await store.close()
        pytest.skip(f"Redis not available at {test_url}: {exc}")

    # Clear test database before test
    await store.client.flushdb()
    try:
        yield store
    finally:
        try:
            await store.client.flushdb()
        finally:
            await store.close()


@pytest.fixture
async def redis_store_with_clock(
    fake_clock: FakeClock,
) -> AsyncGenerator[object, None]:
    """Provide a RedisStorage instance using an injected FakeClock for time."""
    from app.core.storage.redis_store import RedisStorage

    test_url = "redis://127.0.0.1:6379/15"
    store = RedisStorage(url=test_url, clock=fake_clock)
    try:
        await store.client.ping()
    except Exception as exc:
        await store.close()
        pytest.skip(f"Redis not available at {test_url}: {exc}")

    await store.client.flushdb()
    try:
        yield store
    finally:
        try:
            await store.client.flushdb()
        finally:
            await store.close()
