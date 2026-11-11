"""Tests for RFC 7807 problem details error responses.

Per AGENTS.md §9:
429 body (application/problem+json):
- type, title, status, detail, policy_id, retry_after_ms, request_id
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import StorageUnavailable
from app.main import create_app

pytestmark = pytest.mark.api


class TestProblemDetailsResponses:
    """Test RFC 7807 application/problem+json responses."""

    async def test_429_body_shape(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {
            "x-api-key": "test-exhaustion-key",
            "x-request-id": "req-trace-429",
        }
        # Exhaust strict limit (5)
        for _ in range(5):
            await client.get("/api/v1/demo/strict", headers=headers)

        res = await client.get("/api/v1/demo/strict", headers=headers)
        assert res.status_code == 429
        assert res.headers["content-type"] == "application/problem+json"

        data = res.json()
        assert data["type"] == "https://httpstatuses.com/429"
        assert data["title"] == "Too Many Requests"
        assert data["status"] == 429
        assert "strict" in data["detail"]
        assert data["policy_id"] == "strict"
        assert isinstance(data["retry_after_ms"], int)
        assert data["retry_after_ms"] > 0
        assert data["request_id"] == "req-trace-429"

    async def test_400_body_shape_on_invalid_identity(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {
            "x-api-key": "",  # Empty identity
            "x-request-id": "req-trace-400",
        }
        res = await client.get("/api/v1/demo/ping", headers=headers)
        assert res.status_code == 400
        assert res.headers["content-type"] == "application/problem+json"

        data = res.json()
        assert data["type"] == "https://httpstatuses.com/400"
        assert data["title"] == "Bad Request"
        assert data["status"] == 400
        assert "empty or blank" in data["detail"]
        assert data["request_id"] == "req-trace-400"

    async def test_503_body_shape_on_storage_unavailable(
        self,
        api_clock: FakeClock,
        api_settings: Settings,
    ) -> None:
        # Create a route that simulates StorageUnavailable
        app = create_app(settings=api_settings, clock=api_clock)

        @app.get("/test-storage-fail")
        async def fail_route() -> None:
            raise StorageUnavailable("Simulated Redis outage")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            res = await ac.get("/test-storage-fail", headers={"x-request-id": "req-trace-503"})
            assert res.status_code == 503
            assert res.headers["content-type"] == "application/problem+json"
            assert res.headers["retry-after"] == "1"

            data = res.json()
            assert data["type"] == "https://httpstatuses.com/503"
            assert data["title"] == "Service Unavailable"
            assert data["status"] == 503
            assert data["detail"] == "Simulated Redis outage"
            assert data["request_id"] == "req-trace-503"
