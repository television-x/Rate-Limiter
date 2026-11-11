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
from redis.asyncio.client import Pipeline
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import WatchError

from app.core.clock import Clock
from app.core.storage.base import Storage, Transaction


class RedisTransaction:
    """Optimistic transaction for Redis using WATCH / MULTI / EXEC.

    Operates over a dedicated Redis pipeline connection. Commands before MULTI
    (reads) execute immediately under the WATCH guarantee. Commands after MULTI
    (writes) queue in the pipeline and commit atomically on commit().
    """

    def __init__(self, pipe: Pipeline[Any]) -> None:
        self._pipe = pipe
        self._explicit_multi = False
        self._completed = False

    async def get(self, key: str) -> str | None:
        """Read a key's value under the watch guarantee."""
        raw = await self._pipe.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    async def hgetall(self, key: str) -> dict[str, str]:
        """Read all fields of a hash key under the watch guarantee."""
        raw = await self._pipe.hgetall(key)
        if not raw:
            return {}
        result: dict[str, str] = {}
        for k, v in raw.items():
            k_str = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            v_str = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            result[k_str] = v_str
        return result

    async def zrangebyscore(self, key: str, min_score: float, max_score: float) -> list[str]:
        """Read sorted set members in a score range under the watch guarantee."""
        raw = await self._pipe.zrangebyscore(key, min_score, max_score)
        if not raw:
            return []
        return [m.decode("utf-8") if isinstance(m, bytes) else str(m) for m in raw]

    def _ensure_multi(self) -> None:
        """Ensure the pipeline has entered explicit MULTI mode before queuing writes."""
        if not self._explicit_multi:
            self._pipe.multi()
            self._explicit_multi = True

    def set(self, key: str, value: str, px: int | None = None) -> None:
        """Queue a SET command in the transaction."""
        self._ensure_multi()
        self._pipe.set(key, value, px=px)

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        """Queue an HSET command in the transaction."""
        self._ensure_multi()
        self._pipe.hset(key, mapping=mapping)  # type: ignore[arg-type]

    def pexpire(self, key: str, ms: int) -> None:
        """Queue a PEXPIRE command in the transaction."""
        self._ensure_multi()
        self._pipe.pexpire(key, ms)

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        """Queue a ZADD command in the transaction."""
        self._ensure_multi()
        self._pipe.zadd(key, mapping)  # type: ignore[arg-type]

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> None:
        """Queue a ZREMRANGEBYSCORE command in the transaction."""
        self._ensure_multi()
        self._pipe.zremrangebyscore(key, min_score, max_score)

    async def commit(self) -> bool:
        """Execute all queued commands atomically.

        Returns:
            True if committed successfully without conflicts.
            False if a watched key was modified (WatchError) or commit aborted.
        """
        if self._completed:
            return False
        try:
            if not self._explicit_multi:
                # No write commands were queued
                return True
            try:
                res = await self._pipe.execute()
                return res is not None
            except WatchError:
                return False
        finally:
            self._completed = True
            await self._pipe.reset()

    async def abort(self) -> None:
        """Abort the transaction and release connection and watched keys."""
        if not self._completed:
            self._completed = True
            await self._pipe.reset()

    async def __aenter__(self) -> RedisTransaction:
        """Enter transaction context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit transaction context, aborting if uncommitted."""
        await self.abort()


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
        """Begin an optimistic transaction watching the specified keys.

        Uses a dedicated Redis pipeline connection. Watches watch_keys immediately.
        Commands queued after MULTI commit atomically on commit().
        """
        pipe = self._client.pipeline()
        if watch_keys:
            await pipe.watch(*watch_keys)
        return RedisTransaction(pipe)

    async def close(self) -> None:
        """Close Redis connection and disconnect pool."""
        await self._client.close()
        await self._pool.disconnect()
