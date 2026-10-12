"""Token Bucket rate-limiting algorithm.

Design:
    Uses integer milli-tokens to avoid float drift.
    Refills tokens based on elapsed time since last check.
    Denied requests do NOT write state (lazy refill).

    tokens = min(capacity, stored_tokens + elapsed_ms * refill_rate_per_ms)
    if tokens >= cost: allow, subtract cost, write state
    else: deny, no write

Properties:
    - Uses optimistic transactions (WATCH in Redis, versions in memory)
    - Accuracy: exact (within refill granularity)
    - Denied requests cause no conflicts (no write)
    - Tokens stored as integer milli-tokens

Must not import FastAPI, Redis, or Prometheus.
"""

from __future__ import annotations

from app.core.limiter import Limiter
from app.core.models import Accuracy, Algorithm, Decision, DecisionReason, Policy
from app.core.observer import Observer
from app.core.storage.base import Storage

# We store tokens as milli-tokens (1 token = 1000 milli-tokens)
# to avoid floating-point drift over many refill operations.
_MILLI = 1000


class TokenBucketLimiter(Limiter):
    """Token Bucket rate limiter.

    Maintains a bucket with a maximum capacity. Tokens refill at a
    constant rate. Each request consumes a configurable number of
    tokens. If insufficient tokens, the request is denied WITHOUT
    modifying state (the refill still happens lazily on next check).
    """

    def __init__(
        self,
        policy: Policy,
        storage: Storage,
        observer: Observer | None = None,
    ) -> None:
        super().__init__(policy, storage, observer)
        assert policy.algorithm == Algorithm.TOKEN_BUCKET
        assert policy.capacity is not None
        assert policy.refill_per_sec is not None
        self._capacity: int = policy.capacity
        # Convert refill_per_sec to milli-tokens per ms
        self._refill_rate_milli_per_ms: float = (policy.refill_per_sec * _MILLI) / 1000.0
        # TTL: time to fully refill from 0, plus grace
        self._ttl_ms: int = int((self._capacity / policy.refill_per_sec) * 1000) + 5000

    async def check(self, key: str, cost: int = 1) -> Decision:
        """Check rate limit using the token bucket algorithm.

        Uses optimistic transaction:
        1. Watch the bucket hash key
        2. Read current state (tokens, last_refill_ms)
        3. Compute refilled tokens
        4. If enough: deduct and commit
        5. If denied: no write (no conflict possible)
        """
        now_ms = await self._storage.now_ms()

        txn = await self._storage.optimistic([key])
        state = await txn.hgetall(key)

        # Parse stored state or initialize
        if state:
            stored_milli = int(state.get("tokens_milli", str(self._capacity * _MILLI)))
            last_ms = int(state.get("last_refill_ms", str(now_ms)))
        else:
            stored_milli = self._capacity * _MILLI
            last_ms = now_ms

        # Compute refill
        elapsed_ms = max(0, now_ms - last_ms)
        refilled_milli = int(elapsed_ms * self._refill_rate_milli_per_ms)
        current_milli = min(self._capacity * _MILLI, stored_milli + refilled_milli)

        cost_milli = cost * _MILLI

        if current_milli >= cost_milli:
            # Allowed: deduct tokens and commit
            new_milli = current_milli - cost_milli
            txn.hset(
                key,
                {
                    "tokens_milli": str(new_milli),
                    "last_refill_ms": str(now_ms),
                },
            )
            txn.pexpire(key, self._ttl_ms)

            committed = await txn.commit()
            if not committed:
                # Conflict: someone else modified the key.
                # For simplicity in the memory store, we retry internally.
                # The retry.py module (Phase 5) will handle this properly.
                # For now, report the conflict and try again.
                self._observer.on_txn_conflict(Algorithm.TOKEN_BUCKET.value)
                return await self.check(key, cost)

            remaining = new_milli // _MILLI
            # Time to refill one token
            retry_ms = (
                int(_MILLI / self._refill_rate_milli_per_ms)
                if self._refill_rate_milli_per_ms > 0
                else None
            )
            # Time to fully refill
            deficit_milli = (self._capacity * _MILLI) - new_milli
            reset_ms = (
                int(deficit_milli / self._refill_rate_milli_per_ms)
                if self._refill_rate_milli_per_ms > 0
                else None
            )

            decision = Decision(
                allowed=True,
                limit=self._capacity,
                remaining=remaining,
                retry_after_ms=None,
                reset_after_ms=reset_ms,
                algorithm=Algorithm.TOKEN_BUCKET,
                reason=DecisionReason.ALLOWED,
                accuracy=Accuracy.EXACT,
            )
        else:
            # Denied: do NOT write state (no conflict risk)
            # No need to commit the transaction

            remaining = current_milli // _MILLI
            # Time until enough tokens refill for this cost
            deficit_milli = cost_milli - current_milli
            retry_ms = (
                int(deficit_milli / self._refill_rate_milli_per_ms) + 1
                if self._refill_rate_milli_per_ms > 0
                else None
            )
            reset_ms = (
                int((self._capacity * _MILLI - current_milli) / self._refill_rate_milli_per_ms)
                if self._refill_rate_milli_per_ms > 0
                else None
            )

            decision = Decision(
                allowed=False,
                limit=self._capacity,
                remaining=remaining,
                retry_after_ms=retry_ms,
                reset_after_ms=reset_ms,
                algorithm=Algorithm.TOKEN_BUCKET,
                reason=DecisionReason.LIMIT_EXCEEDED,
                accuracy=Accuracy.EXACT,
            )

        self._observer.on_decision(self._policy.id, decision)
        return decision
