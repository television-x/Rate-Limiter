"""Domain models for the rate limiter.

All validation happens at construction time. These models are used by core
algorithms and the storage layer. They must not import FastAPI, Redis, or
Prometheus.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from app.core.errors import InvalidPolicy

# ── Enums ─────────────────────────────────────────────────────────


class Algorithm(enum.StrEnum):
    """Supported rate-limiting algorithms."""

    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW_LOG = "sliding_window_log"
    SLIDING_WINDOW_COUNTER = "sliding_window_counter"
    TOKEN_BUCKET = "token_bucket"


class FailureMode(enum.StrEnum):
    """Behavior when storage is unavailable."""

    OPEN = "open"  # allow, mark degraded
    CLOSED = "closed"  # reject with 503
    LOCAL_FALLBACK = "local_fallback"  # per-instance memory limiter


class ContentionMode(enum.StrEnum):
    """Behavior when optimistic retries are exhausted."""

    REJECT = "reject"
    ALLOW = "allow"


# ENUMS - custom data types that have a fixed set of values
class DecisionReason(enum.StrEnum):
    """Why a rate-limit decision was made."""

    ALLOWED = "ALLOWED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    DEGRADED_ALLOWED = "DEGRADED_ALLOWED"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    CONTENTION_EXHAUSTED = "CONTENTION_EXHAUSTED"


class Accuracy(enum.StrEnum):
    """Whether the remaining/reset values are exact or estimated."""

    EXACT = "exact"
    ESTIMATED = "estimated"


class IdentityKind(enum.StrEnum):
    """Types of client identity used in key construction."""

    USER = "user"
    IP = "ip"
    API_KEY = "apikey"
    ROUTE = "route"


# ── Policy ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Policy:
    """A rate-limiting policy configuration.

    Validated at construction time. Used by the LimiterFactory to
    create the appropriate algorithm instance.

    For window-based algorithms: use ``limit`` and ``window_ms``.
    For token bucket: use ``capacity`` and ``refill_per_sec``.
    """

    id: str
    algorithm: Algorithm

    # Window-based (fixed window, sliding log, sliding counter)
    limit: int | None = None
    window_ms: int | None = None

    # Token bucket
    capacity: int | None = None
    refill_per_sec: float | None = None

    # Shared
    cost_default: int = 1
    on_failure: FailureMode = FailureMode.OPEN
    on_contention: ContentionMode = ContentionMode.REJECT
    scope: frozenset[IdentityKind] = field(default_factory=lambda: frozenset({IdentityKind.IP}))

    def __post_init__(self) -> None:
        """Validate policy configuration at construction time."""
        if not self.id:
            raise InvalidPolicy("", "id must be non-empty")

        if self.cost_default < 1:
            raise InvalidPolicy(self.id, "cost_default must be >= 1")

        if self.algorithm == Algorithm.TOKEN_BUCKET:
            self._validate_token_bucket()
        else:
            self._validate_window_based()

    def _validate_window_based(self) -> None:
        """Validate window-based algorithm parameters."""
        if self.limit is None or self.limit < 1:
            raise InvalidPolicy(self.id, "limit must be a positive integer")
        if self.window_ms is None or self.window_ms < 1000:
            raise InvalidPolicy(self.id, "window_ms must be >= 1000")

    def _validate_token_bucket(self) -> None:
        """Validate token bucket parameters."""
        if self.capacity is None or self.capacity < 1:
            raise InvalidPolicy(self.id, "capacity must be a positive integer")
        if self.refill_per_sec is None or self.refill_per_sec <= 0:
            raise InvalidPolicy(self.id, "refill_per_sec must be > 0")
        if self.capacity < self.cost_default:
            raise InvalidPolicy(self.id, "capacity must be >= cost_default")


# ── Decision ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Decision:
    """The result of a rate-limit check.

    All algorithms return this. Nullable fields indicate that the
    value is not available (e.g. ``remaining`` for degraded mode).
    """

    allowed: bool
    limit: int
    remaining: int | None
    retry_after_ms: int | None
    reset_after_ms: int | None
    algorithm: Algorithm
    reason: DecisionReason
    degraded: bool = False
    accuracy: Accuracy = Accuracy.EXACT


# ── Subject ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Subject:
    """Identifies a client for rate-limiting purposes.

    Contains the resolved identity and its kind, used by the key
    builder to construct the storage key.
    """

    kind: IdentityKind
    identity: str

    def __post_init__(self) -> None:
        if not self.identity:
            raise ValueError("identity must be non-empty")
