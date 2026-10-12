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
