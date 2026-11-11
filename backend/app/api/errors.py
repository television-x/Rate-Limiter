"""Exception classes and RFC 7807 problem details handlers for the HTTP API.

All error responses use application/problem+json per AGENTS.md.
"""

from __future__ import annotations

from typing import Any
from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.errors import ContentionExhausted, InvalidIdentity, StorageUnavailable
from app.core.models import Decision


class RateLimitExceeded(Exception):
    """Exception raised by rate_limit dependency when a request is denied."""

    def __init__(
        self,
        policy_id: str,
        decision: Decision,
        headers: dict[str, str],
        detail: str = "Rate limit exceeded",
    ) -> None:
        super().__init__(detail)
        self.policy_id = policy_id
        self.decision = decision
        self.headers = headers
        self.detail = detail


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handler for 429 Rate Limit Exceeded responses."""
    request_id = getattr(request.state, "request_id", None)
    content: dict[str, Any] = {
        "type": "https://httpstatuses.com/429",
        "title": "Too Many Requests",
        "status": 429,
        "detail": exc.detail or f"Rate limit exceeded for policy '{exc.policy_id}'.",
        "policy_id": exc.policy_id,
        "retry_after_ms": exc.decision.retry_after_ms,
        "request_id": request_id,
    }
    headers = dict(exc.headers)
    headers["Content-Type"] = "application/problem+json"
    return JSONResponse(status_code=429, content=content, headers=headers)


def invalid_identity_handler(request: Request, exc: InvalidIdentity) -> JSONResponse:
    """Handler for 400 Bad Request when client identity is malformed."""
    request_id = getattr(request.state, "request_id", None)
    content: dict[str, Any] = {
        "type": "https://httpstatuses.com/400",
        "title": "Bad Request",
        "status": 400,
        "detail": exc.reason,
        "request_id": request_id,
    }
    return JSONResponse(
        status_code=400,
        content=content,
        headers={"Content-Type": "application/problem+json"},
    )


def storage_unavailable_handler(request: Request, exc: StorageUnavailable) -> JSONResponse:
    """Handler for 503 Service Unavailable when storage backend fails."""
    request_id = getattr(request.state, "request_id", None)
    content: dict[str, Any] = {
        "type": "https://httpstatuses.com/503",
        "title": "Service Unavailable",
        "status": 503,
        "detail": exc.message or "Rate limiter storage backend is unavailable",
        "request_id": request_id,
    }
    return JSONResponse(
        status_code=503,
        content=content,
        headers={"Retry-After": "1", "Content-Type": "application/problem+json"},
    )


def contention_exhausted_handler(request: Request, exc: ContentionExhausted) -> JSONResponse:
    """Handler for 429 when optimistic transaction retry limit is exhausted."""
    request_id = getattr(request.state, "request_id", None)
    content: dict[str, Any] = {
        "type": "https://httpstatuses.com/429",
        "title": "Contention Exhausted",
        "status": 429,
        "detail": exc.message,
        "request_id": request_id,
    }
    return JSONResponse(
        status_code=429,
        content=content,
        headers={"Retry-After": "1", "Content-Type": "application/problem+json"},
    )

