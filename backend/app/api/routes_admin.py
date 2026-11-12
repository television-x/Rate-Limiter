"""Admin and statistics API routes.

Per AGENTS.md §9:
- GET /health/dependencies: Redis latency, breaker state (admin bearer, no keys, no credentials)
- PUT /api/v1/admin/policies/{id}: Update demo policy within bounded ranges (admin bearer)
- GET /api/v1/admin/stats?policy_id=&window_s=60: Rolling throughput & top-N clients (admin bearer)
- POST /api/v1/admin/reset: Reset by exact computed key (never SCAN, admin bearer, status 204)
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.auth import verify_admin_token
from app.api.schemas import (
    CircuitBreakerInfo,
    DependencyHealthResponse,
    PolicyInfo,
    PolicyStatsResponse,
    PolicyUpdateRequest,
    RedisDependencyInfo,
    ResetRequest,
    StatsPoint,
    TopClientInfo,
)
from app.core.key_builder import build_key
from app.core.models import Algorithm, ContentionMode, FailureMode, IdentityKind, Policy
from app.core.storage.base import Storage
from app.policies import DEMO_POLICY_IDS, PolicyForbiddenError, PolicyRegistry
from app.services.stats import StatsService

# Admin router prefixed with /api/v1/admin (and health route)
router = APIRouter(tags=["admin"])


@router.get(
    "/health/dependencies",
    response_model=DependencyHealthResponse,
    dependencies=[Depends(verify_admin_token)],
    summary="Check dependency health, storage latency, and breaker state",
)
async def get_dependency_health(request: Request) -> DependencyHealthResponse:
    """Return storage latency and circuit breaker status.

    Does not return any keys, hostnames, or credentials.
    """
    storage: Storage = request.app.state.storage
    start_time = time.perf_counter()
    connected = False
    latency_ms: float | None = None

    try:
        connected = await storage.ping()
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    except Exception:
        connected = False

    breaker = getattr(request.app.state, "circuit_breaker", None)
    breaker_state = breaker.state.value if breaker else "closed"
    consecutive_failures = breaker.consecutive_failures if breaker else 0
    failure_threshold = breaker.failure_threshold if breaker else 5

    if connected and breaker_state == "closed":
        overall_status = "healthy"
    elif connected and breaker_state == "half_open":
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return DependencyHealthResponse(
        status=overall_status,
        redis=RedisDependencyInfo(connected=connected, latency_ms=latency_ms),
        circuit_breaker=CircuitBreakerInfo(
            state=breaker_state,
            consecutive_failures=consecutive_failures,
            failure_threshold=failure_threshold,
        ),
    )


@router.put(
    "/api/v1/admin/policies/{id}",
    response_model=PolicyInfo,
    dependencies=[Depends(verify_admin_token)],
    summary="Update demo policy configuration within bounded ranges",
)
async def update_demo_policy(
    id: str,
    update: PolicyUpdateRequest,
    request: Request,
) -> PolicyInfo:
    """Update a demo policy configuration at runtime within strictly bounded ranges.

    Persists override to storage and propagates across all application instances.
    """
    registry: PolicyRegistry = request.app.state.policy_registry

    if id not in DEMO_POLICY_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Policy '{id}' is not a demo policy; only demo policies may be updated at runtime.",
        )

    if id not in registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{id}' not found.",
        )

    existing = registry.get(id)

    # Build updated Policy instance merging changes
    try:
        updated_policy = Policy(
            id=existing.id,
            algorithm=existing.algorithm,
            limit=update.limit if update.limit is not None else existing.limit,
            window_ms=update.window_ms if update.window_ms is not None else existing.window_ms,
            capacity=update.capacity if update.capacity is not None else existing.capacity,
            refill_per_sec=update.refill_per_sec
            if update.refill_per_sec is not None
            else existing.refill_per_sec,
            cost_default=update.cost_default
            if update.cost_default is not None
            else existing.cost_default,
            on_failure=FailureMode(update.on_failure)
            if update.on_failure is not None
            else existing.on_failure,
            on_contention=ContentionMode(update.on_contention)
            if update.on_contention is not None
            else existing.on_contention,
            scope=existing.scope,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    storage = getattr(request.app.state, "storage", None)
    try:
        await registry.set_override(updated_policy, storage=storage)
    except PolicyForbiddenError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    effective = registry.get(id)
    return PolicyInfo(
        id=effective.id,
        algorithm=effective.algorithm.value,
        limit=effective.limit,
        window_ms=effective.window_ms,
        capacity=effective.capacity,
        refill_per_sec=effective.refill_per_sec,
        cost_default=effective.cost_default,
        on_failure=effective.on_failure.value,
        on_contention=effective.on_contention.value,
        scope=[s.value for s in effective.scope],
    )


@router.get(
    "/api/v1/admin/stats",
    response_model=PolicyStatsResponse,
    dependencies=[Depends(verify_admin_token)],
    summary="Get rolling per-second throughput and top clients for a policy",
)
async def get_admin_stats(
    request: Request,
    policy_id: str = Query(..., description="Target rate limit policy ID"),
    window_s: int = Query(default=60, ge=1, le=300, description="Rolling time window in seconds"),
    top_n: int = Query(default=10, ge=1, le=100, description="Number of top clients to return"),
) -> PolicyStatsResponse:
    """Retrieve rolling per-second allowed and rejected counts with top-N client breakdowns."""
    registry: PolicyRegistry = request.app.state.policy_registry
    if policy_id not in registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{policy_id}' not found.",
        )

    stats_service: StatsService | None = getattr(request.app.state, "stats_service", None)
    if stats_service is None:
        return PolicyStatsResponse(
            policy_id=policy_id,
            window_s=window_s,
            total_allowed=0,
            total_rejected=0,
            series=[],
            top_clients=[],
        )

    summary = stats_service.get_stats(policy_id=policy_id, window_s=window_s, top_n=top_n)

    return PolicyStatsResponse(
        policy_id=summary.policy_id,
        window_s=summary.window_s,
        total_allowed=summary.total_allowed,
        total_rejected=summary.total_rejected,
        series=[
            StatsPoint(
                timestamp_s=pt.timestamp_s,
                allowed=pt.allowed,
                rejected=pt.rejected,
            )
            for pt in summary.series
        ],
        top_clients=[
            TopClientInfo(
                client_hash=c.client_hash,
                allowed=c.allowed,
                rejected=c.rejected,
                total=c.total,
            )
            for c in summary.top_clients
        ],
    )


@router.post(
    "/api/v1/admin/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_admin_token)],
    summary="Reset rate limit state by exact computed key (never SCAN)",
)
async def reset_rate_limit(
    body: ResetRequest,
    request: Request,
) -> Response:
    """Reset rate limiter state for a client and policy using exact key deletion.

    Never performs pattern-matching SCAN operations.
    """
    storage: Storage = request.app.state.storage
    registry: PolicyRegistry = request.app.state.policy_registry

    if body.key:
        await storage.delete(body.key)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if not body.policy_id or not body.identity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either 'key' or both 'policy_id' and 'identity'.",
        )

    if body.policy_id not in registry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy '{body.policy_id}' not found.",
        )

    policy = registry.get(body.policy_id)
    now_ms = await storage.now_ms()
    kind = IdentityKind(body.kind)

    if policy.algorithm == Algorithm.TOKEN_BUCKET:
        k = build_key(policy.id, kind, body.identity, policy.algorithm, suffix="bucket")
        await storage.delete(k)
    elif policy.algorithm == Algorithm.SLIDING_WINDOW_LOG:
        k = build_key(policy.id, kind, body.identity, policy.algorithm, suffix="log")
        await storage.delete(k)
    elif policy.algorithm == Algorithm.FIXED_WINDOW:
        window_id = now_ms // (policy.window_ms or 60000)
        k = build_key(policy.id, kind, body.identity, policy.algorithm, suffix=str(window_id))
        await storage.delete(k)
    elif policy.algorithm == Algorithm.SLIDING_WINDOW_COUNTER:
        w_ms = policy.window_ms or 10000
        window_id = now_ms // w_ms
        k_curr = build_key(policy.id, kind, body.identity, policy.algorithm, suffix=str(window_id))
        k_prev = build_key(
            policy.id, kind, body.identity, policy.algorithm, suffix=str(window_id - 1)
        )
        await storage.delete(k_curr)
        await storage.delete(k_prev)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
