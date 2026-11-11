"""Policy inspection endpoints.

Per AGENTS.md §9:
- GET /api/v1/policies: Read-only list of effective rate-limiting policies (status 200)
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.schemas import PolicyInfo, PolicyListResponse

router = APIRouter(prefix="/api/v1", tags=["policies"])


@router.get("/policies", response_model=PolicyListResponse)
async def list_policies(request: Request) -> PolicyListResponse:
    """Return all effective rate-limiting policies currently loaded."""
    registry = request.app.state.policy_registry
    policies = registry.list_all()

    items = [
        PolicyInfo(
            id=p.id,
            algorithm=p.algorithm.value,
            limit=p.limit,
            window_ms=p.window_ms,
            capacity=p.capacity,
            refill_per_sec=p.refill_per_sec,
            cost_default=p.cost_default,
            on_failure=p.on_failure.value,
            on_contention=p.on_contention.value,
            scope=[s.value for s in p.scope],
        )
        for p in policies
    ]
    return PolicyListResponse(policies=items)
