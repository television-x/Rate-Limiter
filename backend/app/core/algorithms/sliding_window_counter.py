"""Sliding Window Counter rate-limiting algorithm.

Design:
    Maintains two counters: current window and previous window.
    Estimates the count in a true sliding window using weighted sum:

        estimate = prev_count * (1 - elapsed/window_ms) + curr_count

    The estimate is floored consistently (always int()).

    Uses incr_expiring_peek for atomic increment + peek, then
    compensates with decr if the request should be denied.

Properties:
    - Uses MULTI/EXEC (incr_expiring_peek) — no WATCH needed
    - Accuracy: ESTIMATED (approximation of the true sliding count)
    - Compensation step (decr) is NOT atomic with the increment
    - A crash between incr and decr leaves a phantom increment
      for at most one window period

Must not import FastAPI, Redis, or Prometheus.
"""

from __future__ import annotations

from app.core.limiter import Limiter
from app.core.models import Accuracy, Algorithm, Decision, DecisionReason, Policy
from app.core.observer import Observer
from app.core.storage.base import Storage


class SlidingWindowCounterLimiter(Limiter):
    """Sliding Window Counter rate limiter.

    Approximates the true sliding window count using weighted
    interpolation between two fixed windows. Cheaper than the
    sliding log (O(1) per key vs O(L)), but the count is estimated.
    """

    def __init__(
        self,
        policy: Policy,
        storage: Storage,
        observer: Observer | None = None,
    ) -> None:
        super().__init__(policy, storage, observer)
        assert policy.algorithm == Algorithm.SLIDING_WINDOW_COUNTER
        assert policy.limit is not None
        assert policy.window_ms is not None
        self._limit: int = policy.limit
        self._window_ms: int = policy.window_ms

    async def check(self, key: str, cost: int = 1) -> Decision:
        """Check rate limit using the sliding window counter algorithm.

        Steps:
        1. Compute current and previous window IDs
        2. Atomically increment current window + peek previous window
        3. Compute weighted estimate
        4. If over limit: compensate by decrementing current window
        """
        now_ms = await self._storage.now_ms()
        window_id = now_ms // self._window_ms

        curr_key = f"{key}:{window_id}"
        prev_key = f"{key}:{window_id - 1}"

        # Atomic: increment current + peek previous
        # TTL is 2 * window_ms so the previous window is still around
        ttl = self._window_ms * 2 + 1000
        curr_count, prev_count = await self._storage.incr_expiring_peek(
            curr_key, by=cost, ttl_ms=ttl, peek_key=prev_key
        )

        # Compute elapsed fraction within current window
        window_start = window_id * self._window_ms
        elapsed_ms = now_ms - window_start
        weight = 1.0 - (elapsed_ms / self._window_ms)

        # Weighted estimate (floor consistently)
        estimate = int(prev_count * weight) + curr_count

        # Window boundary timing
        window_end_ms = (window_id + 1) * self._window_ms
        reset_after_ms = max(0, window_end_ms - now_ms)

        if estimate <= self._limit:
            remaining = max(0, self._limit - estimate)
            decision = Decision(
                allowed=True,
                limit=self._limit,
                remaining=remaining,
                retry_after_ms=None,
                reset_after_ms=reset_after_ms,
                algorithm=Algorithm.SLIDING_WINDOW_COUNTER,
                reason=DecisionReason.ALLOWED,
                accuracy=Accuracy.ESTIMATED,
            )
        else:
            # Over limit: compensate by removing what we just added
            # NOTE: This decr is NOT atomic with the earlier incr.
            # A crash between incr and decr leaves a phantom increment
            # for at most one window period. This is documented.
            await self._storage.decr(curr_key, by=cost)

            remaining = 0
            decision = Decision(
                allowed=False,
                limit=self._limit,
                remaining=remaining,
                retry_after_ms=reset_after_ms,
                reset_after_ms=reset_after_ms,
                algorithm=Algorithm.SLIDING_WINDOW_COUNTER,
                reason=DecisionReason.LIMIT_EXCEEDED,
                accuracy=Accuracy.ESTIMATED,
            )

        self._observer.on_decision(self._policy.id, decision)
        return decision
