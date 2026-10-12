"""Abstract storage contract for the rate limiter.

This defines the capability-based interface that all storage backends
must implement. A generic get/set/incr interface is NOT sufficient —
each operation has specific atomicity requirements documented below.

The contract is designed around what the algorithms actually need,
not what a key-value store generically offers.

Must not import FastAPI or Prometheus. Redis is imported only in
redis_store.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol


class Transaction(Protocol):
    """An optimistic transaction context.

    Provides read access to watched keys and queued writes that are
    committed atomically. If any watched key has been modified since
    the transaction started, commit() returns False and nothing is
    written.

    Used by: Token Bucket, Sliding Window Log.
    """

    async def get(self, key: str) -> str | None:
        """Read the value of a key within the transaction.

        This read is performed under the WATCH guarantee: if the key
        changes before commit(), the transaction will fail.

        Args:
            key: The key to read.

        Returns:
            The string value, or None if the key does not exist.
        """
        ...

    async def hgetall(self, key: str) -> dict[str, str]:
        """Read all fields of a hash key within the transaction.

        Subject to the same WATCH guarantee as get().

        Args:
            key: The hash key to read.

        Returns:
            A dict of field->value strings. Empty dict if key doesn't exist.
        """
        ...

    async def zrangebyscore(self, key: str, min_score: float, max_score: float) -> list[str]:
        """Read sorted set members within a score range.

        Subject to the same WATCH guarantee as get().

        Args:
            key: The sorted set key.
            min_score: Minimum score (inclusive).
            max_score: Maximum score (inclusive).

        Returns:
            List of member strings in the score range.
        """
        ...

    def set(self, key: str, value: str, px: int | None = None) -> None:
        """Queue a SET command in the transaction.

        Not executed until commit(). If px is provided, the key will
        expire after px milliseconds.

        Args:
            key: The key to set.
            value: The value to set.
            px: Optional expiry in milliseconds.
        """
        ...

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        """Queue an HSET command in the transaction.

        Args:
            key: The hash key.
            mapping: Field->value pairs to set.
        """
        ...

    def pexpire(self, key: str, ms: int) -> None:
        """Queue a PEXPIRE command in the transaction.

        Args:
            key: The key to set expiry on.
            ms: Expiry time in milliseconds.
        """
        ...

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        """Queue a ZADD command in the transaction.

        Args:
            key: The sorted set key.
            mapping: Member->score pairs to add.
        """
        ...

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> None:
        """Queue removal of sorted set members in a score range.

        Args:
            key: The sorted set key.
            min_score: Minimum score (inclusive).
            max_score: Maximum score (inclusive).
        """
        ...

    async def commit(self) -> bool:
        """Attempt to execute all queued commands atomically.

        Returns:
            True if all commands executed (no conflicts).
            False if a watched key was modified (conflict — nothing
            was written, caller should retry).
        """
        ...


class Storage(ABC):
    """Abstract storage backend for rate limiting.

    Each operation has documented atomicity guarantees. Implementations
    must honor these guarantees or the algorithms will produce
    incorrect results.

    The in-memory implementation is STRONGER than Redis: it uses a
    single lock making it linearizable. Memory tests therefore cannot
    prove Redis concurrency correctness — that requires separate
    Redis-only concurrency tests.
    """

    @abstractmethod
    async def now_ms(self) -> int:
        """Return the current time in milliseconds.

        **Atomicity:** Single authoritative clock for all instances.

        In the Redis implementation, this delegates to Redis TIME to
        ensure all API instances share the same time source.

        In the memory implementation, this delegates to the injected
        Clock.

        Returns:
            Current time in milliseconds since epoch.
        """

    @abstractmethod
    async def incr_expiring(self, key: str, by: int, ttl_ms: int) -> int:
        """Increment a key and set its expiry atomically.

        **Atomicity:** The increment and TTL set are ONE ATOMIC UNIT.
        The TTL is set unconditionally (idempotent) — even if the key
        already has a TTL, it is refreshed. This prevents a key from
        persisting indefinitely if only the increment succeeds.

        **Used by:** Fixed Window.

        **Behavior:**
        - If the key does not exist, it is created with value ``by``
          and TTL ``ttl_ms``.
        - If the key exists, its value is incremented by ``by`` and
          TTL is refreshed to ``ttl_ms``.
        - The returned value is the POST-increment count.

        Args:
            key: The counter key.
            by: The increment amount (positive integer).
            ttl_ms: Expiry in milliseconds (set unconditionally).

        Returns:
            The new value after incrementing.
        """

    @abstractmethod
    async def incr_expiring_peek(
        self, key: str, by: int, ttl_ms: int, peek_key: str
    ) -> tuple[int, int]:
        """Increment a key and peek at another key atomically.

        **Atomicity:** Both operations happen in ONE MULTI/EXEC
        transaction. The returned values are consistent (no
        interleaving between the increment and the peek).

        **Used by:** Sliding Window Counter (increment current window,
        peek at previous window's count).

        **Behavior:**
        - Increments ``key`` by ``by`` with TTL ``ttl_ms``
          (same as incr_expiring).
        - Reads the integer value of ``peek_key`` (0 if not found).
        - Returns both values as a tuple.

        Args:
            key: The counter key to increment.
            by: The increment amount.
            ttl_ms: Expiry for the incremented key.
            peek_key: The key to peek at (read-only).

        Returns:
            Tuple of (new_count_after_incr, peek_value_or_0).
        """

    @abstractmethod
    async def decr(self, key: str, by: int) -> int:
        """Decrement a key atomically.

        **Atomicity:** The decrement itself is atomic. However, it is
        SEPARATE from any preceding incr operation. This means a
        crash between incr and decr leaves a phantom increment that
        over-counts for at most one window period.

        **Used by:** Sliding Window Counter (compensation after
        determining the request should be denied).

        Args:
            key: The counter key.
            by: The decrement amount (positive integer).

        Returns:
            The new value after decrementing. May be negative if
            the key didn't exist or was already 0.
        """

    @abstractmethod
    async def optimistic(self, watch_keys: list[str]) -> Transaction:
        """Begin an optimistic transaction watching the given keys.

        **Atomicity:** The transaction provides:
        1. WATCH on the specified keys (detect modifications).
        2. Read operations that return values consistent with the
           watched state.
        3. Queued write operations.
        4. commit() that either executes ALL writes atomically
           (if no watched keys changed) or NONE (if conflict).

        This is NOT equivalent to a lock. Concurrent transactions on
        the same keys will cause conflicts, requiring bounded retries.

        **Used by:** Token Bucket, Sliding Window Log.

        **Redis implementation:** Uses WATCH + MULTI/EXEC on a
        dedicated connection held for the entire attempt.

        **Memory implementation:** Uses per-key version counters.
        Stronger than Redis (linearizable with a single lock), so
        memory tests cannot prove Redis concurrency correctness.

        Args:
            watch_keys: Keys to watch for modifications.

        Returns:
            A Transaction context for reads and queued writes.
        """

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key.

        **Atomicity:** Single atomic operation.

        **Used by:** Admin reset functionality.

        Args:
            key: The key to delete.

        Returns:
            True if the key existed and was deleted, False otherwise.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release resources held by this storage backend.

        Called during application shutdown. Must be safe to call
        multiple times.
        """
