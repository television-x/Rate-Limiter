"""Tests for client identity extraction, proxy trust, and validation.

Per AGENTS.md §3.4:
- Priority: API key header > X-User-Id (only when DEMO_MODE=true) > client IP
- Trust X-Forwarded-For / X-Real-IP only when direct peer in TRUSTED_PROXIES
- Malformed identity (overlong, control chars) returns 400 application/problem+json
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.storage.memory import MemoryStorage
from app.main import create_app

pytestmark = pytest.mark.api


class TestIdentityPrecedence:
    """Test identity extraction priority hierarchy."""

    async def test_api_key_takes_precedence_over_user_id_and_ip(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {
            "x-api-key": "secret-key-123",
            "x-user-id": "user-456",
        }
        res = await client.get("/api/v1/demo/ping", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["identity_kind"] == "apikey"
        assert data["identity"] == "secret-key-123"

    async def test_user_id_takes_precedence_over_ip_when_demo_mode_true(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {
            "x-user-id": "developer-alice",
        }
        res = await client.get("/api/v1/demo/ping", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["identity_kind"] == "user"
        assert data["identity"] == "developer-alice"

    async def test_ip_fallback_when_no_headers(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/api/v1/demo/ping")
        assert res.status_code == 200
        data = res.json()
        assert data["identity_kind"] == "ip"
        assert data["identity"]  # Non-empty IP string


class TestDemoModeToggle:
    """Test behavior when DEMO_MODE is disabled."""

    async def test_user_id_ignored_when_demo_mode_false(
        self,
        api_clock: FakeClock,
        api_storage: MemoryStorage,
    ) -> None:
        settings = Settings(demo_mode=False, trusted_proxies=["127.0.0.1"])
        app = create_app(settings=settings, storage=api_storage, clock=api_clock)

        transport = httpx.ASGITransport(app=app, client=("192.168.1.50", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            headers = {"x-user-id": "should-be-ignored"}
            res = await ac.get("/api/v1/demo/ping", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["identity_kind"] == "ip"
            assert data["identity"] == "192.168.1.50"


class TestTrustedProxyEvaluation:
    """Test X-Forwarded-For evaluation against trusted proxy list."""

    async def test_forwarded_for_trusted_from_trusted_proxy(
        self,
        api_clock: FakeClock,
        api_storage: MemoryStorage,
    ) -> None:
        settings = Settings(demo_mode=True, trusted_proxies=["127.0.0.1"])
        app = create_app(settings=settings, storage=api_storage, clock=api_clock)

        # Direct peer is 127.0.0.1 (trusted)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            headers = {"x-forwarded-for": "203.0.113.195, 10.0.0.1"}
            res = await ac.get("/api/v1/demo/ping", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["identity_kind"] == "ip"
            assert data["identity"] == "203.0.113.195"

    async def test_forwarded_for_ignored_from_untrusted_peer(
        self,
        api_clock: FakeClock,
        api_storage: MemoryStorage,
    ) -> None:
        settings = Settings(demo_mode=True, trusted_proxies=["127.0.0.1"])
        app = create_app(settings=settings, storage=api_storage, clock=api_clock)

        # Direct peer is untrusted IP
        transport = httpx.ASGITransport(app=app, client=("198.51.100.77", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            headers = {"x-forwarded-for": "203.0.113.195"}
            res = await ac.get("/api/v1/demo/ping", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["identity_kind"] == "ip"
            # Must use the direct peer IP, ignoring the spoofed XFF
            assert data["identity"] == "198.51.100.77"


class TestMalformedIdentityValidation:
    """Test malformed identity handling per AGENTS.md §3.4."""

    async def test_overlong_api_key_returns_400(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {"x-api-key": "a" * 150}  # Limit is 128
        res = await client.get("/api/v1/demo/ping", headers=headers)
        assert res.status_code == 400
        assert res.headers["content-type"] == "application/problem+json"
        data = res.json()
        assert data["status"] == 400
        assert "exceeds maximum length" in data["detail"]

    async def test_control_characters_in_api_key_returns_400(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {"x-api-key": "key\x00with\x1fcontrols"}
        res = await client.get("/api/v1/demo/ping", headers=headers)
        assert res.status_code == 400
        assert res.headers["content-type"] == "application/problem+json"
        data = res.json()
        assert data["status"] == 400
        assert "control characters" in data["detail"]

    async def test_blank_user_id_returns_400(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {"x-user-id": "   "}
        res = await client.get("/api/v1/demo/ping", headers=headers)
        assert res.status_code == 400
        assert res.headers["content-type"] == "application/problem+json"
        data = res.json()
        assert data["status"] == 400
        assert "empty or blank" in data["detail"]
