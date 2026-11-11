"""Fixtures for API test suite."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.storage.memory import MemoryStorage
from app.main import create_app


@pytest.fixture
def api_clock() -> FakeClock:
    """Deterministic FakeClock for API tests."""
    return FakeClock(start_ms=1_000_000_000_000)


@pytest.fixture
def api_storage(api_clock: FakeClock) -> MemoryStorage:
    """Memory storage backed by the test FakeClock."""
    return MemoryStorage(clock=api_clock)


@pytest.fixture
def api_settings() -> Settings:
    """Test configuration settings."""
    return Settings(
        instance_name="api-test-node",
        demo_mode=True,
        trusted_proxies=["127.0.0.1", "10.0.0.1"],
        admin_token="test-secret-token",
        policies_path="policies.yaml",
    )


@pytest.fixture
async def client(
    api_settings: Settings,
    api_storage: MemoryStorage,
    api_clock: FakeClock,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async test client with in-memory storage override."""
    app = create_app(
        settings=api_settings,
        storage=api_storage,
        clock=api_clock,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
