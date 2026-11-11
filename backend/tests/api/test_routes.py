"""Tests for application routes: health, demo, and policy endpoints.

Per AGENTS.md §6 Phase 6:
- All four algorithms selectable per route by config alone
- Health and readiness endpoints
- OpenAPI contract is accurate
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.models import Algorithm
from app.core.storage.memory import MemoryStorage
from app.main import create_app

pytestmark = pytest.mark.api


class TestHealthRoutes:
    """Test /healthz and /readyz probes."""

    async def test_healthz_liveness(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    async def test_readyz_healthy(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/readyz")
        assert res.status_code == 200
        assert res.json() == {"status": "ready"}

    async def test_readyz_unhealthy(
        self,
        api_clock: FakeClock,
        api_settings: Settings,
    ) -> None:
        class FailingStorage(MemoryStorage):
            async def ping(self) -> bool:
                return False

        bad_storage = FailingStorage(clock=api_clock)
        app = create_app(settings=api_settings, storage=bad_storage, clock=api_clock)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            res = await ac.get("/readyz")
            assert res.status_code == 503
            assert res.json() == {"status": "not_ready", "reason": "storage_unavailable"}


class TestDemoRoutesPerAlgorithm:
    """Test all 4 algorithms selectable per route by configuration alone."""

    @pytest.mark.parametrize(
        "algo",
        [
            Algorithm.FIXED_WINDOW,
            Algorithm.TOKEN_BUCKET,
            Algorithm.SLIDING_WINDOW_LOG,
            Algorithm.SLIDING_WINDOW_COUNTER,
        ],
    )
    async def test_algorithm_route_selectable_by_config(
        self,
        client: httpx.AsyncClient,
        algo: Algorithm,
    ) -> None:
        headers = {"x-api-key": f"test-user-{algo.value}"}
        res = await client.get(f"/api/v1/demo/limited/{algo.value}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["message"] == "pong"
        assert data["algorithm"] == algo.value
        assert data["policy_id"] == algo.value
        assert data["identity"] == f"test-user-{algo.value}"

    async def test_unknown_algorithm_returns_404(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/api/v1/demo/limited/non_existent_algo")
        assert res.status_code == 404
        assert "Unknown algorithm" in res.json()["detail"]


class TestPolicyListing:
    """Test /api/v1/policies endpoint."""

    async def test_list_policies(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/api/v1/policies")
        assert res.status_code == 200
        data = res.json()
        assert "policies" in data
        policy_ids = [p["id"] for p in data["policies"]]

        # All 6 configured demo policies should be present
        expected = {
            "default",
            "strict",
            "fixed_window",
            "token_bucket",
            "sliding_window_log",
            "sliding_window_counter",
        }
        assert expected.issubset(set(policy_ids))


class TestOpenAPIContract:
    """Test OpenAPI specification generation."""

    async def test_openapi_json_is_valid(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/openapi.json")
        assert res.status_code == 200
        data = res.json()
        assert data["openapi"].startswith("3.")
        assert data["info"]["title"] == "Distributed Rate Limiter"
        assert "/api/v1/demo/ping" in data["paths"]
        assert "/api/v1/policies" in data["paths"]
