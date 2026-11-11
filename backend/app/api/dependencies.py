"""Rate limiting FastAPI dependencies.

Per AGENTS.md §3.5:
Uses FastAPI dependencies for route-level rate limiting (policy resolution,
identity extraction, header injection, 429 exceptions).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response

from app.api.errors import RateLimitExceeded
from app.api.identity import extract_identity
from app.config import get_settings
from app.core.key_builder import build_key
from app.core.limiter import Limiter, LimiterFactory
from app.core.models import Accuracy, Decision, Policy
from app.core.observer import NullObserver
from app.core.storage.base import Storage


def build_rate_limit_headers(
    decision: Decision,
    policy_id: str,
    policy: Policy,
) -> dict[str, str]:
    """Construct rate limit response headers from a Decision and Policy."""
    headers: dict[str, str] = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Policy": policy_id,
    }

    if decision.remaining is not None:
        headers["X-RateLimit-Remaining"] = str(decision.remaining)

    if decision.reset_after_ms is not None:
        reset_seconds = max(0, math.ceil(decision.reset_after_ms / 1000))
        headers["X-RateLimit-Reset"] = str(reset_seconds)

    if decision.degraded:
        headers["X-RateLimit-Status"] = "degraded"

    if decision.accuracy == Accuracy.ESTIMATED:
        headers["X-RateLimit-Accuracy"] = "estimated"

    if not decision.allowed:
        retry_ms = decision.retry_after_ms if decision.retry_after_ms is not None else 1000
        retry_seconds = max(1, math.ceil(retry_ms / 1000))
        headers["Retry-After"] = str(retry_seconds)

    return headers


async def enforce_rate_limit(
    request: Request,
    response: Response | None,
    policy_id: str,
    cost: int | None = None,
) -> Decision:
    """Evaluate rate limiting for a request, setting headers or raising RateLimitExceeded."""
    settings = getattr(request.app.state, "settings", None) or get_settings()
    subject = extract_identity(request, settings)

    registry = request.app.state.policy_registry
    policy = registry.get(policy_id)

    effective_cost = cost if cost is not None else policy.cost_default
    storage: Storage = request.app.state.storage
    observer = getattr(request.app.state, "observer", None) or NullObserver()
    factory: LimiterFactory = request.app.state.limiter_factory

    limiter: Limiter = factory.create(policy, storage, observer)
    key = build_key(policy_id, subject.kind, subject.identity, policy.algorithm)

    start_time = time.perf_counter()
    decision: Decision = await limiter.check(key, cost=effective_cost)
    duration_ms = (time.perf_counter() - start_time) * 1000

    headers = build_rate_limit_headers(decision, policy_id, policy)

    # Store context on request state for routes and middleware
    request.state.rate_limit_headers = headers
    request.state.rate_limit_decision = decision
    request.state.rate_limit_subject = subject
    request.state.rate_limit_duration_ms = duration_ms

    if response is not None:
        for k, v in headers.items():
            response.headers[k] = v

    if not decision.allowed:
        raise RateLimitExceeded(
            policy_id=policy_id,
            decision=decision,
            headers=headers,
            detail=f"Rate limit exceeded for policy '{policy_id}'.",
        )

    return decision


def rate_limit(
    policy_id: str,
    cost: int | None = None,
) -> Callable[..., Any]:
    """FastAPI dependency factory for rate limiting a route by policy ID."""

    async def dependency(
        request: Request,
        response: Response,
    ) -> Decision:
        return await enforce_rate_limit(request, response, policy_id, cost=cost)

    return dependency
