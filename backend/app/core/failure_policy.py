"""Failure policy resolution and resilient limiter wrapper.

Per AGENTS.md §Phase 7:
- Resolves Policy.on_failure:
  - open: allow, degraded=true, X-RateLimit-Status: degraded
  - closed: 503 with Retry-After (raises StorageUnavailable)
  - local_fallback: per-instance memory limiter, explicitly NOT global, degraded=true
- Resolves Policy.on_contention:
  - reject: raises ContentionExhausted
  - allow: allows with degraded=true
"""

from __future__ import annotations

from app.core.errors import ContentionExhausted, StorageUnavailable
from app.core.limiter import Limiter
from app.core.models import (
    Accuracy,
    ContentionMode,
    Decision,
    DecisionReason,
    FailureMode,
    Policy,
)
from app.core.observer import Observer


def resolve_storage_failure(
    policy: Policy,
    inner_decision: Decision | None = None,
) -> Decision:
    """Resolve a storage failure according to policy.on_failure.

    Args:
        policy: The rate-limiting policy.
        inner_decision: Decision from local fallback limiter, if available.

    Returns:
        A degraded Decision if permitted.

    Raises:
        StorageUnavailable: If policy.on_failure is CLOSED.
    """
    limit_val = policy.limit if policy.limit is not None else (policy.capacity or 0)

    if policy.on_failure == FailureMode.CLOSED:
        raise StorageUnavailable(
            f"Storage unavailable for policy '{policy.id}' (on_failure=closed)"
        )

    if policy.on_failure == FailureMode.LOCAL_FALLBACK and inner_decision is not None:
        reason = (
            DecisionReason.DEGRADED_ALLOWED if inner_decision.allowed else inner_decision.reason
        )
        return Decision(
            allowed=inner_decision.allowed,
            limit=inner_decision.limit,
            remaining=inner_decision.remaining,
            retry_after_ms=inner_decision.retry_after_ms,
            reset_after_ms=inner_decision.reset_after_ms,
            algorithm=policy.algorithm,
            reason=reason,
            degraded=True,
            accuracy=Accuracy.ESTIMATED,
        )

    # FailureMode.OPEN (or LOCAL_FALLBACK without inner decision)
    return Decision(
        allowed=True,
        limit=limit_val,
        remaining=None,
        retry_after_ms=None,
        reset_after_ms=None,
        algorithm=policy.algorithm,
        reason=DecisionReason.DEGRADED_ALLOWED,
        degraded=True,
        accuracy=Accuracy.ESTIMATED,
    )


def resolve_contention_failure(policy: Policy, key: str = "") -> Decision:
    """Resolve a contention exhaustion error according to policy.on_contention.

    Args:
        policy: The rate-limiting policy.
        key: The rate limiter key.

    Returns:
        A degraded Decision if contention mode is ALLOW.

    Raises:
        ContentionExhausted: If policy.on_contention is REJECT.
    """
    limit_val = policy.limit if policy.limit is not None else (policy.capacity or 0)

    if policy.on_contention == ContentionMode.REJECT:
        raise ContentionExhausted(
            key=key,
            attempts=5,
            message=f"Optimistic transaction contention exhausted for policy '{policy.id}'",
            algorithm=policy.algorithm.value,
        )

    # ContentionMode.ALLOW
    return Decision(
        allowed=True,
        limit=limit_val,
        remaining=0,
        retry_after_ms=None,
        reset_after_ms=None,
        algorithm=policy.algorithm,
        reason=DecisionReason.DEGRADED_ALLOWED,
        degraded=True,
        accuracy=Accuracy.ESTIMATED,
    )


class ResilientLimiter(Limiter):
    """A Limiter wrapper that handles storage outages and contention failures gracefully.

    Wraps a primary Limiter with fallback handling governed by Policy.on_failure
    and Policy.on_contention.
    """

    def __init__(
        self,
        primary_limiter: Limiter,
        fallback_limiter: Limiter | None = None,
        observer: Observer | None = None,
    ) -> None:
        super().__init__(
            policy=primary_limiter.policy,
            storage=primary_limiter._storage,
            observer=observer or primary_limiter._observer,
        )
        self._primary = primary_limiter
        self._fallback = fallback_limiter

    @property
    def primary(self) -> Limiter:
        """The primary storage-backed limiter."""
        return self._primary

    @property
    def fallback(self) -> Limiter | None:
        """The optional local in-memory fallback limiter."""
        return self._fallback

    async def check(self, key: str, cost: int = 1) -> Decision:
        """Evaluate rate limit with automated failure policy resolution."""
        try:
            return await self._primary.check(key, cost=cost)
        except StorageUnavailable:
            if self._policy.on_failure == FailureMode.LOCAL_FALLBACK and self._fallback is not None:
                inner_decision = await self._fallback.check(key, cost=cost)
                return resolve_storage_failure(self._policy, inner_decision=inner_decision)
            return resolve_storage_failure(self._policy)
        except ContentionExhausted:
            return resolve_contention_failure(self._policy, key=key)
