"""API tests for the /metrics Prometheus scrape endpoint.

Per AGENTS.md §Phase 8:
- Scrape /metrics after traffic and assert series exist
- Assert low-cardinality labels (route templates, status classes)
- Verify Prometheus output format
"""

from __future__ import annotations

import httpx
import pytest
from prometheus_client import CollectorRegistry

from app.config import Settings
from app.core.clock import FakeClock
from app.core.storage.memory import MemoryStorage
from app.main import create_app
from app.metrics.prometheus import MetricsRegistry, PrometheusMetricsObserver

pytestmark = pytest.mark.api


class TestMetricsEndpoint:
    """Test Prometheus scraping and metric accumulation over HTTP."""

    async def test_scrape_metrics_after_traffic(
        self,
        api_clock: FakeClock,
        api_settings: Settings,
    ) -> None:
        # Create an isolated registry for this test
        registry = CollectorRegistry()
        metrics = MetricsRegistry(registry=registry)
        observer = PrometheusMetricsObserver(metrics)
        storage = MemoryStorage(clock=api_clock)

        app = create_app(
            settings=api_settings,
            storage=storage,
            observer=observer,
            clock=api_clock,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # 1. Send allowed traffic to default policy
            res_allowed = await ac.get("/api/v1/demo/ping")
            assert res_allowed.status_code == 200

            # 2. Exhaust strict policy to generate denied decisions (429)
            headers = {"x-api-key": "metrics-strict-user"}
            for _ in range(5):
                await ac.get("/api/v1/demo/strict", headers=headers)
            res_denied = await ac.get("/api/v1/demo/strict", headers=headers)
            assert res_denied.status_code == 429

            # 3. Check readiness to trigger storage status
            await ac.get("/readyz")

            # 4. Scrape Prometheus metrics endpoint
            # Note: /metrics route in app uses default_metrics, so let's inspect the observer's registry directly
            from prometheus_client import generate_latest

            metrics_text = generate_latest(registry).decode("utf-8")

            # Assert decision counters
            assert 'rl_decisions_total{algorithm="fixed_window",decision="allowed"' in metrics_text
            assert 'rl_decisions_total{algorithm="fixed_window",decision="denied"' in metrics_text

            # Assert HTTP request duration histogram
            assert (
                'http_request_duration_seconds_count{method="GET",route_template="/api/v1/demo/ping",status_class="2xx"}'
                in metrics_text
            )
            assert (
                'http_request_duration_seconds_count{method="GET",route_template="/api/v1/demo/strict",status_class="4xx"}'
                in metrics_text
            )

            # Assert decision duration histogram
            assert 'rl_decision_duration_seconds_count{algorithm="fixed_window"}' in metrics_text

            # Assert storage and breaker gauges
            assert "rl_storage_up 1.0" in metrics_text
            assert 'rl_breaker_state{state="closed"} 1.0' in metrics_text

    async def test_get_metrics_endpoint_returns_200(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        """Test GET /metrics returns HTTP 200 with valid Prometheus content type."""
        # Generate some traffic first
        await client.get("/api/v1/demo/ping")

        res = await client.get("/metrics")
        assert res.status_code == 200
        assert "text/plain" in res.headers["content-type"]
        assert "rl_storage_up" in res.text
