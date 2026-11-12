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


# ── Admin & Statistics Schemas (Phase 9) ──────────────────────────


class PolicyUpdateRequest(BaseModel):
    """Payload to update an effective demo policy within bounded ranges."""

    limit: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description="Window limit count (1 to 10,000)",
    )
    window_ms: int | None = Field(
        default=None,
        ge=1000,
        le=3600000,
        description="Window duration in milliseconds (1,000 to 3,600,000)",
    )
    capacity: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description="Token bucket capacity (1 to 10,000)",
    )
    refill_per_sec: float | None = Field(
        default=None,
        gt=0.0,
        le=1000.0,
        description="Token bucket refill rate per second (0.1 to 1,000)",
    )
    cost_default: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Default cost per request (1 to 1,000)",
    )
    on_failure: str | None = Field(
        default=None,
        description="Failure policy mode ('open', 'closed', 'local_fallback')",
    )
    on_contention: str | None = Field(
        default=None,
        description="Contention policy mode ('reject', 'allow')",
    )


class StatsPoint(BaseModel):
    """A single time series point representing one second."""

    timestamp_s: int
    allowed: int
    rejected: int


class TopClientInfo(BaseModel):
    """Usage summary for a single client identified by its truncated hash."""

    client_hash: str
    allowed: int
    rejected: int
    total: int


class PolicyStatsResponse(BaseModel):
    """Aggregated per-second rate limiting metrics and top clients for a policy."""

    policy_id: str
    window_s: int
    total_allowed: int
    total_rejected: int
    series: list[StatsPoint]
    top_clients: list[TopClientInfo]


class ResetRequest(BaseModel):
    """Request payload to clear rate limiter state for a specific client/policy or key."""

    policy_id: str | None = Field(
        default=None,
        description="Policy ID to reset for the given identity",
    )
    identity: str | None = Field(
        default=None,
        description="Client identity to reset (e.g. IP address or user ID)",
    )
    kind: str = Field(
        default="ip",
        description="Identity kind ('ip', 'user', 'apikey', 'route')",
    )
    key: str | None = Field(
        default=None,
        description="Exact computed storage key to delete (never SCAN)",
    )


class RedisDependencyInfo(BaseModel):
    """Redis dependency health status and latency (no credentials or keys)."""

    connected: bool
    latency_ms: float | None = None


class CircuitBreakerInfo(BaseModel):
    """Circuit breaker status details."""

    state: str
    consecutive_failures: int
    failure_threshold: int


class DependencyHealthResponse(BaseModel):
    """Health information for external dependencies and circuit breaker."""

    status: str
    redis: RedisDependencyInfo
    circuit_breaker: CircuitBreakerInfo
