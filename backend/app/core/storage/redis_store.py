"""Redis storage backend for the distributed rate limiter.

This is the ONLY file in the core layer that imports redis.
Implements the Storage interface using redis-py's asyncio client.

Design & Atomicity:
- Connection pool with explicit connect/socket timeouts.
- Client auto-retry is DISABLED (retry_on_timeout=False) to prevent
  non-idempotent INCR commands from double-counting.
- now_ms: uses Redis TIME command (with optional app-clock override for tests).
- incr_expiring: uses MULTI/EXEC (pipeline(transaction=True)) for atomic
  increment + PEXPIRE.
- incr_expiring_peek: uses MULTI(INCR, PEXPIRE, GET)/EXEC for atomic
  increment of current window and peek at previous window.
- decr: atomic DECRBY (separate from earlier increment, for compensation).
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from app.core.clock import Clock
from app.core.storage.base import Storage, Transaction


class RedisStorage(Storage):
    """Redis storage backend.

    Implements atomic primitives for Fixed Window and Sliding Window Counter
    algorithms via Redis transactions (MULTI/EXEC).

    Args:
        url: Redis connection URL (e.g. "redis://localhost:6379/0").
        connect_timeout_ms: Socket connection timeout in milliseconds.
        socket_timeout_ms: Socket read/write timeout in milliseconds.
        clock: Optional Clock override for testing. If None, uses Redis TIME.
    """

    def __init__(
        self,
        url: str = "redis://127.0.0.1:6379/0",
        connect_timeout_ms: int = 100,
        socket_timeout_ms: int = 100,
        clock: Clock | None = None,
    ) -> None:
        self._url = url
        self._clock_override = clock
        self._connect_timeout = connect_timeout_ms / 1000.0
        self._socket_timeout = socket_timeout_ms / 1000.0

        # Connection pool with explicit timeouts and NO auto-retry on timeout
        # (retrying an INCR command on network timeout would double-count).
        self._pool: ConnectionPool[Any] = ConnectionPool.from_url(
            self._url,
            socket_connect_timeout=self._connect_timeout,
            socket_timeout=self._socket_timeout,
            retry_on_timeout=False,
            decode_responses=False,
        )
        self._client: aioredis.Redis[Any] = aioredis.Redis(connection_pool=self._pool)

    @property
    def client(self) -> aioredis.Redis[Any]:
        """Expose client for integration test assertions (e.g. pttl, ping)."""
        return self._client

    async def now_ms(self) -> int:
        """Return current time in milliseconds.

        If a clock override was injected (for deterministic testing), uses that.
        Otherwise, delegates to Redis TIME as the single authoritative clock
        for all distributed instances.
        """
        if self._clock_override is not None:
            return await self._clock_override.now_ms()

        # Redis TIME returns (seconds: int, microseconds: int)
        seconds, microseconds = await self._client.time()
        return int(seconds) * 1000 + int(microseconds) // 1000

    async def incr_expiring(self, key: str, by: int, ttl_ms: int) -> int:
        """Increment a key and set its TTL in one atomic MULTI/EXEC transaction.

        The TTL is set unconditionally via PEXPIRE (idempotent).
        """
        pipe = self._client.pipeline(transaction=True)
        pipe.incrby(key, by)
        pipe.pexpire(key, ttl_ms)
        results = await pipe.execute()
        return int(results[0])

    async def incr_expiring_peek(
        self, key: str, by: int, ttl_ms: int, peek_key: str
    ) -> tuple[int, int]:
        """Atomically increment current window and peek at previous window.

        Executed in a single MULTI/EXEC pipeline so no interleaving can occur.
        """
        pipe = self._client.pipeline(transaction=True)
        pipe.incrby(key, by)
        pipe.pexpire(key, ttl_ms)
        pipe.get(peek_key)
        results = await pipe.execute()

        new_val = int(results[0])
        peek_raw = results[2]
        peek_val = int(peek_raw) if peek_raw is not None else 0
        return new_val, peek_val

    async def decr(self, key: str, by: int) -> int:
        """Atomic decrement for compensation."""
        result = await self._client.decrby(key, by)
        return int(result)

    async def delete(self, key: str) -> bool:
        """Delete a key atomically."""
        count = await self._client.delete(key)
        return bool(count > 0)

    async def pttl(self, key: str) -> int:
        """Return remaining TTL in milliseconds (-2 if key does not exist, -1 if no TTL)."""
        return int(await self._client.pttl(key))

    async def optimistic(self, watch_keys: list[str]) -> Transaction:
        """Optimistic transaction via WATCH/MULTI/EXEC (Phase 5)."""
        raise NotImplementedError(
            "Optimistic WATCH transactions for Redis are implemented in Phase 5."
        )

    async def close(self) -> None:
        """Close Redis connection and disconnect pool."""
        await self._client.close()
        await self._pool.disconnect()
