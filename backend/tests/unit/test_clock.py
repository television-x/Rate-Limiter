"""Unit tests for the Clock abstraction.

Tests FakeClock deterministic behavior and SystemClock sanity.
"""

from __future__ import annotations

import pytest

from app.core.clock import FakeClock, SystemClock


@pytest.mark.unit
class TestFakeClock:
    """Verify FakeClock deterministic time control."""

    async def test_default_start(self) -> None:
        clock = FakeClock()
        assert await clock.now_ms() == 1_000_000_000_000

    async def test_custom_start(self) -> None:
        clock = FakeClock(start_ms=5000)
        assert await clock.now_ms() == 5000

    async def test_advance(self) -> None:
        clock = FakeClock(start_ms=1000)
        clock.advance(500)
        assert await clock.now_ms() == 1500

    async def test_advance_multiple(self) -> None:
        clock = FakeClock(start_ms=1000)
        clock.advance(100)
        clock.advance(200)
        assert await clock.now_ms() == 1300

    async def test_set_absolute(self) -> None:
        clock = FakeClock(start_ms=1000)
        clock.set(9999)
        assert await clock.now_ms() == 9999

    async def test_advance_negative(self) -> None:
        """Clock regression: algorithms handle via max(0, delta)."""
        clock = FakeClock(start_ms=5000)
        clock.advance(-1000)
        assert await clock.now_ms() == 4000

    async def test_consistent_reads(self) -> None:
        """Multiple reads without advance return the same value."""
        clock = FakeClock(start_ms=42000)
        t1 = await clock.now_ms()
        t2 = await clock.now_ms()
        assert t1 == t2 == 42000


@pytest.mark.unit
class TestSystemClock:
    """Sanity check for SystemClock."""

    async def test_returns_positive_int(self) -> None:
        clock = SystemClock()
        result = await clock.now_ms()
        assert isinstance(result, int)
        assert result > 0

    async def test_approximate_epoch(self) -> None:
        """System clock should return something near current epoch-ms."""
        clock = SystemClock()
        result = await clock.now_ms()
        # Should be after 2024-01-01 and before 2100-01-01
        assert result > 1_704_067_200_000
        assert result < 4_102_444_800_000
