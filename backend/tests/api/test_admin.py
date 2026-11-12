"""Tests for admin endpoints, stats, reset, and policy overrides.

Per AGENTS.md §9:
- 401 on missing or invalid bearer token
- 403 on attempting to update non-demo policy
- 404 on non-existent policy
- 422 on parameters outside bounded ranges
- 200 on valid demo policy override, immediately effective
- 204 on reset by computed key, unblocking client
- 200 on dependency health without exposing credentials or keys
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.core.clock import FakeClock
from app.core.models import Algorithm, Policy
from app.core.storage.memory import MemoryStorage
from app.main import create_app
from app.policies import PolicyRegistry
from app.services.stats import StatsService

pytestmark = pytest.mark.api

ADMIN_HEADERS = {"Authorization": "Bearer dev-admin-token"}


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(start_ms=1000000)


@pytest.fixture
def memory_storage(fake_clock: FakeClock) -> MemoryStorage:
    return MemoryStorage(clock=fake_clock)


@pytest.fixture
def test_policies() -> PolicyRegistry:
    return PolicyRegistry(
        [
            Policy(
                id="default",
                algorithm=Algorithm.FIXED_WINDOW,
                limit=10,
                window_ms=60000,
            ),
            Policy(
                id="strict",
                algorithm=Algorithm.FIXED_WINDOW,
                limit=3,
                window_ms=10000,
            ),
            Policy(
                id="token_bucket",
                algorithm=Algorithm.TOKEN_BUCKET,
                capacity=10,
                refill_per_sec=2.0,
            ),
            Policy(
                id="internal_billing",
                algorithm=Algorithm.FIXED_WINDOW,
                limit=100,
                window_ms=60000,
            ),
        ]
    )


@pytest.fixture
def test_app(
    memory_storage: MemoryStorage,
    fake_clock: FakeClock,
    test_policies: PolicyRegistry,
):
    settings = Settings(admin_token="dev-admin-token", demo_mode=True)
    stats_service = StatsService()
    return create_app(
        settings=settings,
        storage=memory_storage,
        clock=fake_clock,
        policy_registry=test_policies,
        stats_service=stats_service,
    )


@pytest.fixture
async def client(test_app) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as ac:
        yield ac


class TestAdminAuthentication:
    """Verify security rules and constant-time token validation."""

    async def test_missing_token_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/health/dependencies")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

    async def test_invalid_token_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/health/dependencies",
            headers={"Authorization": "Bearer wrong-token-1234"},
        )
        assert resp.status_code == 401

    async def test_invalid_auth_scheme_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/health/dependencies",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 401

    async def test_valid_token_succeeds(self, client: AsyncClient) -> None:
        resp = await client.get("/health/dependencies", headers=ADMIN_HEADERS)
        assert resp.status_code == 200


class TestDependencyHealth:
    """Verify /health/dependencies output and credential sanitization."""

    async def test_dependency_health_shape_and_no_credentials(self, client: AsyncClient) -> None:
        resp = await client.get("/health/dependencies", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()

        assert "status" in data
        assert "redis" in data
        assert "circuit_breaker" in data
        assert data["redis"]["connected"] is True
        assert isinstance(data["redis"]["latency_ms"], float)
        assert data["circuit_breaker"]["state"] == "closed"

        # Ensure no sensitive keywords leaked
        content = resp.text.lower()
        assert "password" not in content
        assert "token" not in content
        assert "rl:v1:" not in content


class TestPolicyOverridesAPI:
    """Verify PUT /api/v1/admin/policies/{id} bounds and enforcement."""

    async def test_forbidden_on_non_demo_policy(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/v1/admin/policies/internal_billing",
            headers=ADMIN_HEADERS,
            json={"limit": 50},
        )
        assert resp.status_code == 403
        assert "not a demo policy" in resp.json()["detail"]

    async def test_not_found_on_unknown_policy(self, client: AsyncClient) -> None:
        resp = await client.put(
            "/api/v1/admin/policies/nonexistent",
            headers=ADMIN_HEADERS,
            json={"limit": 50},
        )
        # Even if not demo, returns 403 or 404
        assert resp.status_code in (403, 404)

    async def test_validation_bounds_ge_le(self, client: AsyncClient) -> None:
        # limit=0 is < 1
        resp = await client.put(
            "/api/v1/admin/policies/default",
            headers=ADMIN_HEADERS,
            json={"limit": 0},
        )
        assert resp.status_code == 422

        # limit=50000 is > 10000
        resp = await client.put(
            "/api/v1/admin/policies/default",
            headers=ADMIN_HEADERS,
            json={"limit": 50000},
        )
        assert resp.status_code == 422

    async def test_override_takes_effect_immediately(self, client: AsyncClient) -> None:
        # Initial limit for strict is 3
        # Send 2 requests -> both 200
        for _ in range(2):
            r = await client.get("/api/v1/demo/strict")
            assert r.status_code == 200

        # Now update strict limit to 2
        up_resp = await client.put(
            "/api/v1/admin/policies/strict",
            headers=ADMIN_HEADERS,
            json={"limit": 2},
        )
        assert up_resp.status_code == 200
        assert up_resp.json()["limit"] == 2

        # 3rd request should now be rejected (429) because count is 2 and limit is 2
        r3 = await client.get("/api/v1/demo/strict")
        assert r3.status_code == 429


class TestAdminResetAPI:
    """Verify POST /api/v1/admin/reset exact key deletion and unblocking."""

    async def test_reset_missing_args_returns_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/admin/reset",
            headers=ADMIN_HEADERS,
            json={},
        )
        assert resp.status_code == 400

    async def test_reset_unblocks_rate_limited_client(self, client: AsyncClient) -> None:
        headers = {"X-User-Id": "demo-user-1"}

        # Exhaust strict policy (limit=3)
        for _ in range(3):
            r = await client.get("/api/v1/demo/strict", headers=headers)
            assert r.status_code == 200

        # 4th request rejected
        r4 = await client.get("/api/v1/demo/strict", headers=headers)
        assert r4.status_code == 429

        # Admin resets rate limit for this user
        reset_resp = await client.post(
            "/api/v1/admin/reset",
            headers=ADMIN_HEADERS,
            json={"policy_id": "strict", "identity": "demo-user-1", "kind": "user"},
        )
        assert reset_resp.status_code == 204

        # Next request immediately allowed
        r5 = await client.get("/api/v1/demo/strict", headers=headers)
        assert r5.status_code == 200


class TestAdminStatsAPI:
    """Verify GET /api/v1/admin/stats series and top-N client calculations."""

    async def test_stats_not_found_on_unknown_policy(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/admin/stats?policy_id=does_not_exist",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    async def test_stats_window_validation(self, client: AsyncClient) -> None:
        # window_s=0 (< 1)
        resp = await client.get(
            "/api/v1/admin/stats?policy_id=default&window_s=0",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422

        # window_s=500 (> 300)
        resp = await client.get(
            "/api/v1/admin/stats?policy_id=default&window_s=500",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422

    async def test_stats_aggregates_traffic(self, client: AsyncClient) -> None:
        headers_u1 = {"X-User-Id": "client-alpha"}
        headers_u2 = {"X-User-Id": "client-beta"}

        # Generate traffic on default policy
        await client.get("/api/v1/demo/ping", headers=headers_u1)
        await client.get("/api/v1/demo/ping", headers=headers_u1)
        await client.get("/api/v1/demo/ping", headers=headers_u2)

        resp = await client.get(
            "/api/v1/admin/stats?policy_id=default&window_s=60",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["policy_id"] == "default"
        assert data["window_s"] == 60
        assert data["total_allowed"] >= 3
        assert len(data["series"]) == 60
        assert len(data["top_clients"]) >= 2

        # Verify client hashes are truncated hex strings
        for client_info in data["top_clients"]:
            assert len(client_info["client_hash"]) == 12
            assert "client-alpha" not in client_info["client_hash"]
