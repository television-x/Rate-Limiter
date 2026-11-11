"""Data Transfer Objects (DTOs) and response schemas for the HTTP API.

Per AGENTS.md, these schemas belong exclusively to the API layer and are
never imported by core/.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetail(BaseModel):
    """RFC 7807 Problem Details for HTTP APIs (application/problem+json)."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        default="about:blank",
        description="URI reference that identifies the problem type",
    )
    title: str = Field(description="Short, human-readable summary of the problem type")
    status: int = Field(description="HTTP status code")
    detail: str = Field(description="Human-readable explanation specific to this occurrence")
    policy_id: str | None = Field(
        default=None,
        description="Rate limit policy ID that triggered this error",
    )
    retry_after_ms: int | None = Field(
        default=None,
        description="Time to wait before retrying in milliseconds",
    )
    request_id: str | None = Field(
        default=None,
        description="Request tracking identifier",
    )


class PingResponse(BaseModel):
    """Response returned by demo ping endpoints."""

    message: str = "pong"
    policy_id: str
    algorithm: str
    identity: str
    identity_kind: str
    time_ms: int


class PolicyInfo(BaseModel):
    """Details of a single rate-limiting policy."""

    id: str
    algorithm: str
    limit: int | None = None
    window_ms: int | None = None
    capacity: int | None = None
    refill_per_sec: float | None = None
    cost_default: int = 1
    on_failure: str
    on_contention: str
    scope: list[str]


class PolicyListResponse(BaseModel):
    """List of effective policies available in the system."""

    policies: list[PolicyInfo]

