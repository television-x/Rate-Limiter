"""Observability middleware for cross-cutting request context.

Per AGENTS.md §3.5:
- Injects or propagates X-Request-ID
- Sets X-Served-By with the server instance identifier
- Emits Server-Timing header (e.g. Server-Timing: limiter;dur=1.23)
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Middleware attaching request ID, instance identifier, and timing metrics."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # 1. Request ID propagation or generation
        request_id = request.headers.get("x-request-id")
        if not request_id or not request_id.strip():
            request_id = uuid.uuid4().hex
        else:
            request_id = request_id.strip()

        request.state.request_id = request_id

        # 2. Timing
        start_time = time.perf_counter()

        response = await call_next(request)

        # 3. Add X-Request-ID and X-Served-By headers
        response.headers["X-Request-ID"] = request_id

        # Instance name from app state if available, else settings
        instance_name = "api-local"
        if hasattr(request.app.state, "settings") and request.app.state.settings is not None:
            instance_name = request.app.state.settings.instance_name
        else:
            instance_name = get_settings().instance_name

        response.headers["X-Served-By"] = instance_name

        # 4. Server-Timing header
        server_timings: list[str] = []
        if hasattr(request.state, "rate_limit_duration_ms"):
            dur = request.state.rate_limit_duration_ms
            server_timings.append(f"limiter;dur={dur:.2f}")

        total_dur = (time.perf_counter() - start_time) * 1000
        server_timings.append(f"app;dur={total_dur:.2f}")

        timing_str = ", ".join(server_timings)
        if "Server-Timing" in response.headers:
            response.headers["Server-Timing"] += f", {timing_str}"
        else:
            response.headers["Server-Timing"] = timing_str

        return response
