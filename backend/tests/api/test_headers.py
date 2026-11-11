"""Tests for rate limiting HTTP headers.

Per AGENTS.md §9:
- Retry-After (ceil seconds, on 429/503)
- X-RateLimit-Limit
- X-RateLimit-Remaining
- X-RateLimit-Reset (delta seconds)
- X-Served-By
- Server-Timing: limiter;dur=...
- X-RateLimit-Accuracy: estimated for sliding window counter
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.api


class TestRateLimitHeadersAllowed:
    """Test headers present on allowed (200 OK) responses."""

    async def test_headers_present_on_allowed_request(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/api/v1/demo/ping")
        assert res.status_code == 200

        # Rate limit headers
        assert "x-ratelimit-limit" in res.headers
        assert res.headers["x-ratelimit-limit"] == "60"

        assert "x-ratelimit-remaining" in res.headers
        assert res.headers["x-ratelimit-remaining"] == "59"

        assert "x-ratelimit-reset" in res.headers
        reset_val = int(res.headers["x-ratelimit-reset"])
        assert reset_val >= 0

        # Observability headers
        assert "x-served-by" in res.headers
        assert res.headers["x-served-by"] == "api-test-node"

        assert "x-request-id" in res.headers
        assert len(res.headers["x-request-id"]) > 0

        assert "server-timing" in res.headers
        assert "limiter;dur=" in res.headers["server-timing"]

    async def test_remaining_quota_decrements(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {"x-api-key": "test-remaining-key"}
        res1 = await client.get("/api/v1/demo/strict", headers=headers)
        assert res1.status_code == 200
        assert res1.headers["x-ratelimit-remaining"] == "4"

        res2 = await client.get("/api/v1/demo/strict", headers=headers)
        assert res2.status_code == 200
        assert res2.headers["x-ratelimit-remaining"] == "3"

    async def test_custom_request_id_propagated(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        custom_id = "trace-uuid-12345678"
        res = await client.get("/api/v1/demo/ping", headers={"x-request-id": custom_id})
        assert res.status_code == 200
        assert res.headers["x-request-id"] == custom_id


class TestRateLimitHeadersDenied:
    """Test headers present on denied (429 Too Many Requests) responses."""

    async def test_headers_present_on_429(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        headers = {"x-api-key": "strict-key-exhaustion"}
        # Strict policy limit is 5
        for _ in range(5):
            res = await client.get("/api/v1/demo/strict", headers=headers)
            assert res.status_code == 200

        # 6th request must be 429
        denied = await client.get("/api/v1/demo/strict", headers=headers)
        assert denied.status_code == 429
        assert denied.headers["content-type"] == "application/problem+json"

        # RateLimit headers MUST appear on 429
        assert "x-ratelimit-limit" in denied.headers
        assert denied.headers["x-ratelimit-limit"] == "5"

        assert "x-ratelimit-remaining" in denied.headers
        assert denied.headers["x-ratelimit-remaining"] == "0"

        assert "x-ratelimit-reset" in denied.headers

        # Retry-After must be ceil seconds >= 1
        assert "retry-after" in denied.headers
        retry_val = int(denied.headers["retry-after"])
        assert retry_val >= 1

        # Observability headers on 429
        assert "x-served-by" in denied.headers
        assert denied.headers["x-served-by"] == "api-test-node"

        assert "x-request-id" in denied.headers


class TestAccuracyHeader:
    """Test accuracy distinction (exact vs estimated)."""

    async def test_sliding_window_counter_reports_estimated(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/api/v1/demo/limited/sliding_window_counter")
        assert res.status_code == 200
        assert res.headers.get("x-ratelimit-accuracy") == "estimated"

    async def test_fixed_window_omits_estimated_accuracy(
        self,
        client: httpx.AsyncClient,
    ) -> None:
        res = await client.get("/api/v1/demo/limited/fixed_window")
        assert res.status_code == 200
        assert res.headers.get("x-ratelimit-accuracy") != "estimated"
