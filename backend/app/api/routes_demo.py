"""Demo endpoints demonstrating rate limiting across algorithms.

Per AGENTS.md §9:
- GET /api/v1/demo/ping: default policy (60 req / 60s)
- GET /api/v1/demo/strict: strict policy (5 req / 10s)
- GET /api/v1/demo/limited/{algorithm}: one policy per algorithm
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.dependencies import enforce_rate_limit, rate_limit
from app.api.schemas import PingResponse
from app.core.models import Algorithm, Decision

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


async def _get_time_ms(request: Request) -> int:
    """Get current time from storage, falling back to system clock during outages."""
    try:
        storage = getattr(request.app.state, "storage", None)
        if storage is not None:
            return int(await storage.now_ms())
    except Exception:
        pass
    return int(time.time() * 1000)


@router.get("/ping", response_model=PingResponse)
async def demo_ping(
    request: Request,
    response: Response,
    decision: Decision = Depends(rate_limit("default")),
) -> PingResponse:
    """Default demo endpoint with standard rate limit."""
    subject = request.state.rate_limit_subject
    now_ms = await _get_time_ms(request)
    return PingResponse(
        message="pong",
        policy_id="default",
        algorithm=decision.algorithm.value,
        identity=subject.identity,
        identity_kind=subject.kind.value,
        time_ms=now_ms,
    )


@router.get("/strict", response_model=PingResponse)
async def demo_strict(
    request: Request,
    response: Response,
    decision: Decision = Depends(rate_limit("strict")),
) -> PingResponse:
    """Strict demo endpoint with aggressive rate limit."""
    subject = request.state.rate_limit_subject
    now_ms = await _get_time_ms(request)
    return PingResponse(
        message="pong",
        policy_id="strict",
        algorithm=decision.algorithm.value,
        identity=subject.identity,
        identity_kind=subject.kind.value,
        time_ms=now_ms,
    )


@router.get("/limited/{algorithm}", response_model=PingResponse)
async def demo_limited_by_algorithm(
    algorithm: str,
    request: Request,
    response: Response,
) -> PingResponse:
    """Demo endpoint targeting a specific algorithm's policy."""
    try:
        algo_enum = Algorithm(algorithm)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown algorithm: '{algorithm}'. Supported: {[a.value for a in Algorithm]}",
        ) from None

    policy_id = algo_enum.value
    registry = request.app.state.policy_registry
    if policy_id not in registry:
        raise HTTPException(
            status_code=404,
            detail=f"No policy configured for algorithm '{algorithm}'",
        )

    decision = await enforce_rate_limit(request, response, policy_id)
    subject = request.state.rate_limit_subject
    now_ms = await _get_time_ms(request)

    return PingResponse(
        message="pong",
        policy_id=policy_id,
        algorithm=decision.algorithm.value,
        identity=subject.identity,
        identity_kind=subject.kind.value,
        time_ms=now_ms,
    )
