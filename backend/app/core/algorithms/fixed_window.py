"""Fixed Window rate-limiting algorithm.

Design:
    window_id = now_ms // window_ms
    key = base_key + ":" + str(window_id)
    count = incr_expiring(key, cost, ttl=window_ms + grace)
    allowed = count <= limit

Properties:
    - Atomicity: relies on incr_expiring (single atomic op)
    - Accuracy: exact within a window
    - Boundary burst: up to 2L requests across a window boundary
    - Rejected requests still increment the counter
    - TTL includes a grace period to avoid premature expiry

Must not import FastAPI, Redis, or Prometheus.
"""

from __future__ import annotations

from app.core.limiter import Limiter
from app.core.models import Accuracy, Algorithm, Decision, DecisionReason, Policy
from app.core.observer import Observer
from app.core.storage.base import Storage

# Grace period added to TTL to prevent premature key expiry at
# window boundaries. This ensures the counter survives for the
# full window even under clock drift.
_TTL_GRACE_MS = 1000


class FixedWindowLimiter(Limiter):
    """Fixed Window rate limiter.

    Divides time into fixed windows of ``window_ms`` milliseconds.
    Each window has an independent counter. A request is allowed if
    the counter after incrementing is <= limit.

    Known limitation: up to 2L requests can pass across a window
    boundary (the last requests of one window + the first of the
    next). This is inherent to fixed-window and is documented.
    """

    def __init__(
        self,
        policy: Policy,
        storage: Storage,
        observer: Observer | None = None,
    ) -> None:
        super().__init__(policy, storage, observer)
        assert policy.algorithm == Algorithm.FIXED_WINDOW
        assert policy.limit is not None
        assert policy.window_ms is not None
        self._limit: int = policy.limit
        self._window_ms: int = policy.window_ms

    async def check(self, key: str, cost: int = 1) -> Decision:
        """Check rate limit using the fixed window algorithm.

        Steps:
        1. Get current time from storage (authoritative clock)
        2. Compute window ID: now_ms // window_ms
        3. Build window-specific key
        4. Atomically increment counter with TTL
        5. Decide based on count vs limit

        Note: rejected requests still increment the counter. This is
        standard fixed-window behavior — the counter reflects total
        arrivals, not just allowed ones.
        """
        now_ms = await self._storage.now_ms()
        window_id = now_ms // self._window_ms
        window_key = f"{key}:{window_id}"

        # Atomic increment + TTL set
        ttl = self._window_ms + _TTL_GRACE_MS
        count = await self._storage.incr_expiring(window_key, by=cost, ttl_ms=ttl)

        # Window boundary timing
        window_end_ms = (window_id + 1) * self._window_ms
        reset_after_ms = max(0, window_end_ms - now_ms)

        allowed = count <= self._limit
        remaining = max(0, self._limit - count)

        if allowed:
            decision = Decision(
                allowed=True,
                limit=self._limit,
                remaining=remaining,
                retry_after_ms=None,
                reset_after_ms=reset_after_ms,
                algorithm=Algorithm.FIXED_WINDOW,
                reason=DecisionReason.ALLOWED,
                accuracy=Accuracy.EXACT,
            )
        else:
            decision = Decision(
                allowed=False,
                limit=self._limit,
                remaining=0,
                retry_after_ms=reset_after_ms,
                reset_after_ms=reset_after_ms,
                algorithm=Algorithm.FIXED_WINDOW,
                reason=DecisionReason.LIMIT_EXCEEDED,
                accuracy=Accuracy.EXACT,
            )

        self._observer.on_decision(self._policy.id, decision)
        return decision
