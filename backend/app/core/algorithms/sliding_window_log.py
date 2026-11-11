"""Sliding Window Log rate-limiting algorithm.

Design:
    Maintains a sorted set of request timestamps. Each entry is
    unique via "{timestamp_ms}:{uuid4hex}".
    1. Trim entries older than window_ms
    2. Count remaining entries
    3. If count < limit: add new entry and commit
    4. If count >= limit: deny without writing

Properties:
    - Uses optimistic transactions (WATCH in Redis, versions in memory)
    - Accuracy: exact
    - Memory: O(limit) per client per policy
    - Same-millisecond requests are unique via UUID suffix

Must not import FastAPI, Redis, or Prometheus.
"""

from __future__ import annotations

import uuid

from app.core.limiter import Limiter
from app.core.models import Accuracy, Algorithm, Decision, DecisionReason, Policy
from app.core.observer import Observer
from app.core.storage.base import Storage
from app.core.storage.retry import DEFAULT_MAX_ATTEMPTS, run_with_retry


class SlidingWindowLogLimiter(Limiter):
    """Sliding Window Log rate limiter.

    Tracks every request timestamp in a sorted set. Provides exact
    counting within the sliding window, at the cost of O(L) memory
    per client.
    """

    def __init__(
        self,
        policy: Policy,
        storage: Storage,
        observer: Observer | None = None,
        max_retries: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        super().__init__(policy, storage, observer)
        assert policy.algorithm == Algorithm.SLIDING_WINDOW_LOG
        assert policy.limit is not None
        assert policy.window_ms is not None
        self._limit: int = policy.limit
        self._window_ms: int = policy.window_ms
        self._max_retries: int = max_retries

    async def check(self, key: str, cost: int = 1) -> Decision:
        """Check rate limit using the sliding window log algorithm.

        Uses optimistic transaction with bounded retries:
        1. Watch the sorted set key
        2. Trim entries older than (now - window_ms)
        3. Count remaining entries
        4. If count + cost <= limit: add entries and commit
        5. If denied: abort transaction (no write)
        6. On conflict: retry up to max_retries with jitter
        """

        async def _attempt(attempt_idx: int) -> tuple[bool, Decision]:
            now_ms = await self._storage.now_ms()
            window_start = now_ms - self._window_ms

            txn = await self._storage.optimistic([key])
            try:
                # Read all entries in the current window
                members = await txn.zrangebyscore(key, float(window_start), float(now_ms))
                current_count = len(members)

                if current_count + cost <= self._limit:
                    # Allowed: trim old entries and add new ones
                    txn.zremrangebyscore(key, 0, float(window_start - 1))

                    # Add `cost` entries, each with a unique member
                    for _ in range(cost):
                        member = f"{now_ms}:{uuid.uuid4().hex}"
                        txn.zadd(key, {member: float(now_ms)})

                    # Set TTL to window_ms + grace
                    txn.pexpire(key, self._window_ms + 1000)

                    committed = await txn.commit()
                    if not committed:
                        dummy = Decision(
                            allowed=False,
                            limit=self._limit,
                            remaining=0,
                            retry_after_ms=None,
                            reset_after_ms=None,
                            algorithm=Algorithm.SLIDING_WINDOW_LOG,
                            reason=DecisionReason.LIMIT_EXCEEDED,
                            accuracy=Accuracy.EXACT,
                        )
                        return False, dummy

                    remaining = max(0, self._limit - (current_count + cost))
                    reset_after_ms = self._window_ms

                    decision = Decision(
                        allowed=True,
                        limit=self._limit,
                        remaining=remaining,
                        retry_after_ms=None,
                        reset_after_ms=reset_after_ms,
                        algorithm=Algorithm.SLIDING_WINDOW_LOG,
                        reason=DecisionReason.ALLOWED,
                        accuracy=Accuracy.EXACT,
                    )
                    self._observer.on_decision(self._policy.id, decision)
                    return True, decision
                else:
                    # Denied: no write needed
                    await txn.abort()

                    remaining = max(0, self._limit - current_count)

                    # Calculate when the oldest entry in the window will expire
                    # to make room for this request
                    retry_after_ms = self._window_ms  # Conservative default
                    if members:
                        oldest_ts = min(int(m.split(":")[0]) for m in members)
                        retry_after_ms = max(1, (oldest_ts + self._window_ms) - now_ms)

                    decision = Decision(
                        allowed=False,
                        limit=self._limit,
                        remaining=0,
                        retry_after_ms=retry_after_ms,
                        reset_after_ms=self._window_ms,
                        algorithm=Algorithm.SLIDING_WINDOW_LOG,
                        reason=DecisionReason.LIMIT_EXCEEDED,
                        accuracy=Accuracy.EXACT,
                    )
                    self._observer.on_decision(self._policy.id, decision)
                    return True, decision
            except Exception:
                await txn.abort()
                raise

        return await run_with_retry(
            _attempt,
            key=key,
            algorithm=Algorithm.SLIDING_WINDOW_LOG.value,
            observer=self._observer,
            max_attempts=self._max_retries,
        )
