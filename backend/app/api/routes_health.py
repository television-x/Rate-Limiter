"""Health and readiness check endpoints.

Per AGENTS.md §9:
- GET /healthz: Liveness check (process only, status 200)
- GET /readyz: Readiness check (initialized + storage PING cached ~1s, status 200 / 503)
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])

_CACHE_TTL_SECONDS = 1.0


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Process liveness probe."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness probe checking storage connectivity (cached ~1s)."""
    now = time.monotonic()
    cache: tuple[float, bool] = getattr(request.app.state, "_cached_ready_status", (0.0, False))
    last_check, last_healthy = cache

    if last_check > 0 and (now - last_check < _CACHE_TTL_SECONDS):
        is_healthy = last_healthy
    else:
        storage = getattr(request.app.state, "storage", None)
        observer = getattr(request.app.state, "observer", None)
        if storage is None:
            is_healthy = False
        else:
            try:
                is_healthy = await storage.ping()
            except Exception:
                is_healthy = False

        if observer is not None and hasattr(observer, "on_storage_status"):
            observer.on_storage_status(is_healthy)

        request.app.state._cached_ready_status = (now, is_healthy)

    if is_healthy:
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "reason": "storage_unavailable"},
    )
