"""Clock abstraction for the rate limiter.

Provides a protocol for obtaining the current time in milliseconds,
with a real implementation (SystemClock) and a test double (FakeClock).

Must not import FastAPI, Redis, or any outer layer.
"""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    """Protocol for obtaining the current time in milliseconds.

    All rate-limiting algorithms use this to get a consistent time
    source. In production, this delegates to the storage layer
    (Redis TIME for distributed consistency). For unit tests,
    FakeClock provides deterministic control.
    """

    async def now_ms(self) -> int:
        """Return the current time in milliseconds since epoch."""
        ...


class SystemClock:
    """Clock implementation using the system clock.

    Used when no distributed clock (Redis TIME) is available,
    e.g. in local-only or test configurations.
    """

    async def now_ms(self) -> int:
        """Return the current system time in milliseconds."""
        return int(time.time() * 1000)


class FakeClock:
    """Deterministic clock for testing.

    Allows tests to set and advance time precisely, ensuring
    reproducible and non-flaky test behavior.
    """

    def __init__(self, start_ms: int = 1_000_000_000_000) -> None:
        """Initialize with a fixed starting time.

        Args:
            start_ms: Starting time in milliseconds. Defaults to a
                      realistic epoch-ms value.
        """
        self._now_ms: int = start_ms

    async def now_ms(self) -> int:
        """Return the current fake time in milliseconds."""
        return self._now_ms

    def set(self, ms: int) -> None:
        """Set the clock to an absolute time.

        Args:
            ms: The time in milliseconds to set.
        """
        self._now_ms = ms

    def advance(self, ms: int) -> None:
        """Advance the clock by a delta.

        Args:
            ms: Number of milliseconds to advance. May be negative
                to simulate clock regression (handled by algorithms
                via max(0, ...)).
        """
        self._now_ms += ms
