"""In-memory storage implementation for the rate limiter.

This implementation is STRONGER than Redis: a single asyncio lock makes
it linearizable. Memory tests therefore CANNOT prove Redis concurrency
correctness — use the Redis-only concurrency suite for that.

Features:
- Per-key version counters for optimistic transaction conflict detection.
- Conflict injection hook for deterministic retry testing.
- TTL support via expiry timestamps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.clock import Clock
from app.core.storage.base import Storage, Transaction


@dataclass
class _Entry:
    """A stored value with optional expiry."""

    value: Any
    expires_at_ms: int | None = None
    version: int = 0


class MemoryTransaction:
    """Optimistic transaction for the memory store.

    Uses version counters to detect conflicts. If any watched key's
    version changes between the start and commit, the commit fails.
    """

    def __init__(
        self,
        store: MemoryStorage,
        watch_keys: list[str],
        watched_versions: dict[str, int],
    ) -> None:
        self._store = store
        self._watch_keys = watch_keys
        self._watched_versions = watched_versions
        self._commands: list[tuple[str, tuple[Any, ...]]] = []

    async def get(self, key: str) -> str | None:
        """Read a key's value under the watch guarantee."""
        entry = self._store._get_live(key)
        if entry is None:
            return None
        return str(entry.value)

    async def hgetall(self, key: str) -> dict[str, str]:
        """Read all fields of a hash key under the watch guarantee."""
        entry = self._store._get_live(key)
        if entry is None:
            return {}
        if not isinstance(entry.value, dict):
            return {}
        return {k: str(v) for k, v in entry.value.items()}

    async def zrangebyscore(self, key: str, min_score: float, max_score: float) -> list[str]:
        """Read sorted set members in a score range."""
        entry = self._store._get_live(key)
        if entry is None:
            return []
        if not isinstance(entry.value, dict):
            return []
        return [member for member, score in entry.value.items() if min_score <= score <= max_score]

    def set(self, key: str, value: str, px: int | None = None) -> None:
        """Queue a SET command."""
        self._commands.append(("set", (key, value, px)))

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        """Queue an HSET command."""
        self._commands.append(("hset", (key, mapping)))

    def pexpire(self, key: str, ms: int) -> None:
        """Queue a PEXPIRE command."""
        self._commands.append(("pexpire", (key, ms)))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        """Queue a ZADD command."""
        self._commands.append(("zadd", (key, mapping)))

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> None:
        """Queue a ZREMRANGEBYSCORE command."""
        self._commands.append(("zremrangebyscore", (key, min_score, max_score)))

    async def commit(self) -> bool:
        """Execute all queued commands if no watched keys changed.

        Returns:
            True if committed, False if conflict detected.
        """
        async with self._store._lock:
            # Check for conflicts via version comparison
            for key in self._watch_keys:
                current_version = self._store._version_of(key)
                if current_version != self._watched_versions.get(key, 0):
                    return False

            # Check conflict injection hook
            if self._store._should_inject_conflict():
                return False

            # All versions match — execute queued commands
            now_ms = await self._store._clock.now_ms()
            for cmd, args in self._commands:
                self._store._execute_command(cmd, args, now_ms)

            return True


class MemoryStorage(Storage):
    """In-memory storage backend with conflict injection.

    Thread-safe via asyncio.Lock. Linearizable (stronger than Redis).

    Args:
        clock: Clock implementation for time. Use FakeClock in tests.
        conflict_hook: Optional callable returning True to inject a
                       conflict on the next optimistic commit. Used
                       for deterministic retry testing.
    """

    def __init__(
        self,
        clock: Clock,
        conflict_hook: Callable[[], bool] | None = None,
    ) -> None:
        self._clock = clock
        self._data: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._conflict_hook = conflict_hook
        self._conflict_count: int = 0

    # ── Public Storage interface ─────────────────────────────────

    async def now_ms(self) -> int:
        """Return the current time from the injected clock."""
        return await self._clock.now_ms()

    async def incr_expiring(self, key: str, by: int, ttl_ms: int) -> int:
        """Atomic increment with TTL set.

        Atomicity: single lock acquisition covers both operations.
        """
        async with self._lock:
            now = await self._clock.now_ms()
            entry = self._get_live_locked(key, now)
            if entry is None:
                entry = _Entry(value=0, version=0)
                self._data[key] = entry

            entry.value = int(entry.value) + by
            entry.expires_at_ms = now + ttl_ms
            entry.version += 1
            return int(entry.value)

    async def incr_expiring_peek(
        self, key: str, by: int, ttl_ms: int, peek_key: str
    ) -> tuple[int, int]:
        """Atomic increment + peek under one lock.

        Atomicity: single lock acquisition ensures consistency.
        """
        async with self._lock:
            now = await self._clock.now_ms()

            # Increment
            entry = self._get_live_locked(key, now)
            if entry is None:
                entry = _Entry(value=0, version=0)
                self._data[key] = entry
            entry.value = int(entry.value) + by
            entry.expires_at_ms = now + ttl_ms
            entry.version += 1

            # Peek
            peek_entry = self._get_live_locked(peek_key, now)
            peek_val = int(peek_entry.value) if peek_entry is not None else 0

            return int(entry.value), peek_val

    async def decr(self, key: str, by: int) -> int:
        """Atomic decrement."""
        async with self._lock:
            now = await self._clock.now_ms()
            entry = self._get_live_locked(key, now)
            if entry is None:
                entry = _Entry(value=0, version=0)
                self._data[key] = entry

            entry.value = int(entry.value) - by
            entry.version += 1
            return int(entry.value)

    async def optimistic(self, watch_keys: list[str]) -> Transaction:
        """Begin an optimistic transaction.

        Captures current versions of watched keys. On commit, checks
        that no versions have changed.
        """
        async with self._lock:
            watched_versions = {key: self._version_of(key) for key in watch_keys}
        return MemoryTransaction(self, watch_keys, watched_versions)

    async def delete(self, key: str) -> bool:
        """Delete a key."""
        async with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    async def close(self) -> None:
        """Clear all data."""
        self._data.clear()

    # ── Conflict injection ───────────────────────────────────────

    def set_conflict_hook(self, hook: Callable[[], bool] | None) -> None:
        """Set or clear the conflict injection hook.

        Args:
            hook: A callable returning True to trigger a conflict on
                  the next optimistic commit. The hook is called once
                  per commit attempt.
        """
        self._conflict_hook = hook

    def _should_inject_conflict(self) -> bool:
        """Check and consume the conflict injection hook."""
        if self._conflict_hook is not None and self._conflict_hook():
            self._conflict_count += 1
            return True
        return False

    @property
    def conflict_count(self) -> int:
        """Number of injected conflicts (for test assertions)."""
        return self._conflict_count

    # ── Internal helpers ─────────────────────────────────────────

    def _get_live(self, key: str) -> _Entry | None:
        """Get a non-expired entry (without lock — caller must hold lock or
        accept the race in reads-under-watch which is fine for the
        memory store's sequential guarantee)."""
        entry = self._data.get(key)
        if entry is None:
            return None
        # Lazy expiry — can't check without lock + time, so we skip
        # expiry check here. The locked version is used for mutations.
        return entry

    def _get_live_locked(self, key: str, now_ms: int) -> _Entry | None:
        """Get a non-expired entry (must hold lock)."""
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at_ms is not None and now_ms >= entry.expires_at_ms:
            del self._data[key]
            return None
        return entry

    def _version_of(self, key: str) -> int:
        """Get the current version of a key (0 if not found)."""
        entry = self._data.get(key)
        if entry is None:
            return 0
        return entry.version

    def _execute_command(self, cmd: str, args: tuple[Any, ...], now_ms: int) -> None:
        """Execute a single queued transaction command.

        Must be called under the lock.
        """
        if cmd == "set":
            key, value, px = args
            expires = now_ms + px if px is not None else None
            existing = self._data.get(key)
            version = existing.version + 1 if existing else 1
            self._data[key] = _Entry(value=value, expires_at_ms=expires, version=version)

        elif cmd == "hset":
            key, mapping = args
            entry = self._data.get(key)
            if entry is None:
                entry = _Entry(value={}, version=0)
                self._data[key] = entry
            if not isinstance(entry.value, dict):
                entry.value = {}
            entry.value.update(mapping)
            entry.version += 1

        elif cmd == "pexpire":
            key, ms = args
            entry = self._data.get(key)
            if entry is not None:
                entry.expires_at_ms = now_ms + ms

        elif cmd == "zadd":
            key, mapping = args
            entry = self._data.get(key)
            if entry is None:
                entry = _Entry(value={}, version=0)
                self._data[key] = entry
            if not isinstance(entry.value, dict):
                entry.value = {}
            entry.value.update(mapping)
            entry.version += 1

        elif cmd == "zremrangebyscore":
            key, min_score, max_score = args
            entry = self._data.get(key)
            if entry is not None and isinstance(entry.value, dict):
                to_remove = [m for m, s in entry.value.items() if min_score <= s <= max_score]
                for m in to_remove:
                    del entry.value[m]
                if to_remove:
                    entry.version += 1
