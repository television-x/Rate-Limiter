"""API tests for failure handling, circuit breaker, and degraded headers.

Per AGENTS.md §Phase 7:
- open: allow, degraded=true, X-RateLimit-Status: degraded
- closed: 503 with Retry-After
- local_fallback: per-instance memory limiter, explicitly not global, degraded=true
- Contention exhaustion follows on_contention
- Recovery without app restart
"""

from __future__ import annotations

import httpx
import pytest
from tests.unit.test_failure_policy import FaultInjectingStorage

from app.config import Settings
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerStorage
from app.core.clock import FakeClock
from app.core.models import Algorithm, FailureMode, Policy
from app.core.storage.memory import MemoryStorage
from app.main import create_app
from app.policies import PolicyRegistry

pytestmark = pytest.mark.api


class TestAPIFailurePolicyModes:
    """Test HTTP responses across different Policy.on_failure modes."""

    async def test_fail_open_mode_returns_200_with_degraded_header(
        self,
        api_clock: FakeClock,
        api_settings: Settings,
    ) -> None:
        policy = Policy(
            id="fixed_window",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=5,
            window_ms=10000,
            on_failure=FailureMode.OPEN,
        )
        registry = PolicyRegistry([policy])

        # Underlying storage is down
        failing_storage = FaultInjectingStorage(MemoryStorage(clock=api_clock), should_fail=True)
        app = create_app(
            settings=api_settings,
            storage=failing_storage,
            policy_registry=registry,
            clock=api_clock,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            res = await ac.get("/api/v1/demo/limited/fixed_window")
            # Should still be allowed, but marked degraded
            assert res.status_code == 200
            assert res.headers.get("x-ratelimit-status") == "degraded"

    async def test_fail_closed_mode_returns_503_with_retry_after(
        self,
        api_clock: FakeClock,
        api_settings: Settings,
    ) -> None:
        policy = Policy(
            id="fixed_window",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=5,
            window_ms=10000,
            on_failure=FailureMode.CLOSED,
        )
        registry = PolicyRegistry([policy])

        failing_storage = FaultInjectingStorage(MemoryStorage(clock=api_clock), should_fail=True)
        app = create_app(
            settings=api_settings,
            storage=failing_storage,
            policy_registry=registry,
            clock=api_clock,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            res = await ac.get("/api/v1/demo/limited/fixed_window")
            assert res.status_code == 503
            assert res.headers.get("retry-after") == "1"
            assert res.headers.get("content-type") == "application/problem+json"
            assert res.json()["status"] == 503

    async def test_local_fallback_mode_enforces_limit_with_degraded_header(
        self,
        api_clock: FakeClock,
        api_settings: Settings,
    ) -> None:
        policy = Policy(
            id="fixed_window",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=2,  # Small limit for testing exhaustion
            window_ms=10000,
            on_failure=FailureMode.LOCAL_FALLBACK,
        )
        registry = PolicyRegistry([policy])

        failing_storage = FaultInjectingStorage(MemoryStorage(clock=api_clock), should_fail=True)
        local_fallback = MemoryStorage(clock=api_clock)

        app = create_app(
            settings=api_settings,
            storage=failing_storage,
            fallback_storage=local_fallback,
            policy_registry=registry,
            clock=api_clock,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            headers = {"x-api-key": "local-fallback-user"}

            # 1st request -> 200 OK (degraded)
            r1 = await ac.get("/api/v1/demo/limited/fixed_window", headers=headers)
            assert r1.status_code == 200
            assert r1.headers.get("x-ratelimit-status") == "degraded"
            assert r1.headers.get("x-ratelimit-remaining") == "1"

            # 2nd request -> 200 OK (degraded)
            r2 = await ac.get("/api/v1/demo/limited/fixed_window", headers=headers)
            assert r2.status_code == 200
            assert r2.headers.get("x-ratelimit-status") == "degraded"
            assert r2.headers.get("x-ratelimit-remaining") == "0"

            # 3rd request -> 429 Too Many Requests (local limit exceeded, marked degraded)
            r3 = await ac.get("/api/v1/demo/limited/fixed_window", headers=headers)
            assert r3.status_code == 429
            assert r3.headers.get("x-ratelimit-status") == "degraded"


class TestBreakerOutageAndRecoveryWithoutRestart:
    """Test full outage simulation, circuit tripping, and recovery without app restart."""

    async def test_outage_trips_breaker_and_recovers(
        self,
        api_clock: FakeClock,
        api_settings: Settings,
    ) -> None:
        policy = Policy(
            id="fixed_window",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=10,
            window_ms=10000,
            on_failure=FailureMode.OPEN,
        )
        registry = PolicyRegistry([policy])

        raw_storage = FaultInjectingStorage(MemoryStorage(clock=api_clock), should_fail=False)
        breaker = CircuitBreaker(clock=api_clock, failure_threshold=2, cooldown_ms=5000)
        cb_storage = CircuitBreakerStorage(raw_storage, breaker)

        app = create_app(
            settings=api_settings,
            storage=cb_storage,
            circuit_breaker=breaker,
            policy_registry=registry,
            clock=api_clock,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # 1. Normal state (storage is healthy)
            res1 = await ac.get("/api/v1/demo/limited/fixed_window")
            assert res1.status_code == 200
            assert "x-ratelimit-status" not in res1.headers  # Not degraded

            # 2. Outage begins: storage fails
            raw_storage.should_fail = True

            # Request 1 fails -> failure count 1
            res_fail1 = await ac.get("/api/v1/demo/limited/fixed_window")
            assert res_fail1.status_code == 200
            assert res_fail1.headers.get("x-ratelimit-status") == "degraded"

            # Request 2 fails -> failure count 2 -> trips breaker to OPEN
            res_fail2 = await ac.get("/api/v1/demo/limited/fixed_window")
            assert res_fail2.status_code == 200
            assert res_fail2.headers.get("x-ratelimit-status") == "degraded"
            assert breaker.state.value == "open"

            # 3. Breaker is OPEN -> fast-fails without hitting raw_storage
            calls_before = raw_storage.call_count
            res_fast_fail = await ac.get("/api/v1/demo/limited/fixed_window")
            assert res_fast_fail.status_code == 200
            assert res_fast_fail.headers.get("x-ratelimit-status") == "degraded"
            # raw_storage should NOT have been called because breaker intercepted it
            assert raw_storage.call_count == calls_before

            # 4. Outage ends, storage recovers
            raw_storage.should_fail = False

            # Advance clock past 5000ms cooldown
            api_clock.advance(5001)

            # 5. Probe request in HALF_OPEN succeeds and closes the breaker
            res_probe = await ac.get("/api/v1/demo/limited/fixed_window")
            assert res_probe.status_code == 200
            assert "x-ratelimit-status" not in res_probe.headers  # Fully recovered!
            assert breaker.state.value == "closed"
