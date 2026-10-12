"""Unit tests for the Fixed Window rate-limiting algorithm.

Tests per AGENTS.md Phase 2:
- Exactly L allowed then rejected
- New window resets
- Boundary burst demo (2L)
- cost > 1
- Expiry via FakeClock
- Invalid config rejected
- Decision fields correctness
"""

from __future__ import annotations

import pytest

from app.core.algorithms.fixed_window import FixedWindowLimiter
from app.core.clock import FakeClock
from app.core.errors import InvalidPolicy
from app.core.models import (
    Accuracy,
    Algorithm,
    Decision,
    DecisionReason,
    Policy,
)
from app.core.observer import NullObserver
from app.core.storage.memory import MemoryStorage

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(start_ms=1_000_000_000_000)


@pytest.fixture
def storage(clock: FakeClock) -> MemoryStorage:
    return MemoryStorage(clock=clock)


def make_limiter(
    storage: MemoryStorage,
    limit: int = 5,
    window_ms: int = 10_000,
    policy_id: str = "test",
    cost_default: int = 1,
) -> FixedWindowLimiter:
    """Helper to create a FixedWindowLimiter with given params."""
    policy = Policy(
        id=policy_id,
        algorithm=Algorithm.FIXED_WINDOW,
        limit=limit,
        window_ms=window_ms,
        cost_default=cost_default,
    )
    return FixedWindowLimiter(policy, storage)


# ── Core behavior ─────────────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowBasic:
    """Test fundamental fixed window behavior."""

    async def test_exactly_limit_allowed_then_rejected(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """Exactly L requests allowed, then all rejected."""
        limiter = make_limiter(storage, limit=5)
        key = "test:key"

        # First 5 should be allowed
        for i in range(5):
            decision = await limiter.check(key)
            assert decision.allowed is True, f"Request {i + 1} should be allowed"
            assert decision.remaining == 5 - (i + 1)

        # 6th should be rejected
        decision = await limiter.check(key)
        assert decision.allowed is False
        assert decision.remaining == 0
        assert decision.reason == DecisionReason.LIMIT_EXCEEDED

    async def test_new_window_resets(self, clock: FakeClock, storage: MemoryStorage) -> None:
        """Moving to a new window resets the counter."""
        limiter = make_limiter(storage, limit=3, window_ms=10_000)
        key = "test:key"

        # Exhaust the limit
        for _ in range(3):
            await limiter.check(key)
        decision = await limiter.check(key)
        assert decision.allowed is False

        # Advance past the window
        clock.advance(10_001)

        # Should be allowed again
        decision = await limiter.check(key)
        assert decision.allowed is True
        assert decision.remaining == 2

    async def test_decision_fields_on_allowed(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """Verify all Decision fields for an allowed request."""
        limiter = make_limiter(storage, limit=10, window_ms=5_000)
        decision = await limiter.check("test:key")

        assert decision.allowed is True
        assert decision.limit == 10
        assert decision.remaining == 9
        assert decision.retry_after_ms is None
        assert decision.reset_after_ms is not None
        assert decision.reset_after_ms > 0
        assert decision.algorithm == Algorithm.FIXED_WINDOW
        assert decision.reason == DecisionReason.ALLOWED
        assert decision.degraded is False
        assert decision.accuracy == Accuracy.EXACT

    async def test_decision_fields_on_rejected(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """Verify all Decision fields for a rejected request."""
        limiter = make_limiter(storage, limit=1)
        key = "test:key"

        await limiter.check(key)  # Use up the limit
        decision = await limiter.check(key)

        assert decision.allowed is False
        assert decision.limit == 1
        assert decision.remaining == 0
        assert decision.retry_after_ms is not None
        assert decision.retry_after_ms > 0
        assert decision.reset_after_ms is not None
        assert decision.algorithm == Algorithm.FIXED_WINDOW
        assert decision.reason == DecisionReason.LIMIT_EXCEEDED
        assert decision.accuracy == Accuracy.EXACT


# ── Cost > 1 ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowCost:
    """Test variable cost requests."""

    async def test_cost_greater_than_one(self, clock: FakeClock, storage: MemoryStorage) -> None:
        """A request with cost=3 consumes 3 of the limit."""
        limiter = make_limiter(storage, limit=5)
        key = "test:key"

        decision = await limiter.check(key, cost=3)
        assert decision.allowed is True
        assert decision.remaining == 2

        decision = await limiter.check(key, cost=3)
        assert decision.allowed is False  # 3 + 3 = 6 > 5

    async def test_cost_exactly_limit(self, clock: FakeClock, storage: MemoryStorage) -> None:
        """A single request with cost = limit should be allowed."""
        limiter = make_limiter(storage, limit=10)
        key = "test:key"

        decision = await limiter.check(key, cost=10)
        assert decision.allowed is True
        assert decision.remaining == 0

    async def test_cost_exceeds_limit(self, clock: FakeClock, storage: MemoryStorage) -> None:
        """A request with cost > limit is rejected immediately."""
        limiter = make_limiter(storage, limit=5)
        key = "test:key"

        decision = await limiter.check(key, cost=6)
        assert decision.allowed is False


# ── Boundary burst ────────────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowBoundaryBurst:
    """Demonstrate the 2L boundary burst limitation.

    This is inherent to fixed-window and documented in AGENTS.md.
    """

    async def test_boundary_burst_allows_2l(self, clock: FakeClock, storage: MemoryStorage) -> None:
        """Up to 2L requests can pass across a window boundary.

        Strategy: send L requests at the end of window N, then L
        requests at the start of window N+1. All 2L are allowed
        despite the per-window limit being L.
        """
        limit = 5
        window_ms = 10_000
        limiter = make_limiter(storage, limit=limit, window_ms=window_ms)
        key = "test:burst"

        # Position at end of window: 1ms before boundary
        # clock starts at 1_000_000_000_000
        # window_id = 1_000_000_000_000 // 10_000 = 100_000_000_000
        # window ends at (100_000_000_000 + 1) * 10_000 = 1_000_000_010_000
        # Go to 1ms before boundary
        clock.set(1_000_000_009_999)

        # Send L requests at end of window
        allowed_window1 = 0
        for _ in range(limit):
            d = await limiter.check(key)
            if d.allowed:
                allowed_window1 += 1
        assert allowed_window1 == limit

        # Cross the boundary
        clock.set(1_000_000_010_000)

        # Send L requests at start of new window
        allowed_window2 = 0
        for _ in range(limit):
            d = await limiter.check(key)
            if d.allowed:
                allowed_window2 += 1
        assert allowed_window2 == limit

        # Total: 2L allowed across the boundary
        assert allowed_window1 + allowed_window2 == 2 * limit


# ── Expiry ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowExpiry:
    """Test TTL-based counter expiry via FakeClock."""

    async def test_counter_expires_after_window(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """Counter is gone after window_ms + grace."""
        limiter = make_limiter(storage, limit=3, window_ms=5_000)
        key = "test:expiry"

        # Use all 3
        for _ in range(3):
            await limiter.check(key)

        # Still in same window — rejected
        decision = await limiter.check(key)
        assert decision.allowed is False

        # Advance past the window
        clock.advance(6_001)  # past window_ms + grace

        # New window — allowed
        decision = await limiter.check(key)
        assert decision.allowed is True
        assert decision.remaining == 2


# ── Reset timing ──────────────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowResetTiming:
    """Test reset_after_ms correctness."""

    async def test_reset_after_ms_counts_down(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """reset_after_ms should decrease as time passes within the window."""
        limiter = make_limiter(storage, limit=10, window_ms=10_000)
        key = "test:timing"

        d1 = await limiter.check(key)
        reset1 = d1.reset_after_ms

        clock.advance(3_000)
        d2 = await limiter.check(key)
        reset2 = d2.reset_after_ms

        assert reset1 is not None
        assert reset2 is not None
        assert reset2 < reset1
        # Should have decreased by ~3000ms
        assert abs((reset1 - reset2) - 3_000) < 100

    async def test_retry_after_equals_reset_after_on_reject(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """On rejection, retry_after_ms == reset_after_ms."""
        limiter = make_limiter(storage, limit=1)
        key = "test:retry"

        await limiter.check(key)
        decision = await limiter.check(key)

        assert decision.allowed is False
        assert decision.retry_after_ms == decision.reset_after_ms


# ── Different keys ────────────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowIsolation:
    """Test that different keys are independent."""

    async def test_different_keys_independent(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """Requests to different keys don't interfere."""
        limiter = make_limiter(storage, limit=2)

        for _ in range(2):
            await limiter.check("key_a")

        # key_a exhausted
        d = await limiter.check("key_a")
        assert d.allowed is False

        # key_b still fresh
        d = await limiter.check("key_b")
        assert d.allowed is True
        assert d.remaining == 1


# ── Invalid config ────────────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowConfig:
    """Test that invalid configurations are rejected at Policy level."""

    def test_rejects_zero_limit(self) -> None:
        with pytest.raises(InvalidPolicy):
            Policy(id="bad", algorithm=Algorithm.FIXED_WINDOW, limit=0, window_ms=1000)

    def test_rejects_small_window(self) -> None:
        with pytest.raises(InvalidPolicy):
            Policy(id="bad", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=500)

    def test_rejects_missing_limit(self) -> None:
        with pytest.raises(InvalidPolicy):
            Policy(id="bad", algorithm=Algorithm.FIXED_WINDOW, window_ms=1000)


# ── Observer integration ──────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowObserver:
    """Test that the observer is notified on decisions."""

    async def test_observer_called_on_allow(self, clock: FakeClock, storage: MemoryStorage) -> None:
        """Observer.on_decision is called for allowed requests."""
        decisions: list[Decision] = []

        class TestObserver(NullObserver):
            def on_decision(self, policy_id: str, decision: Decision) -> None:
                decisions.append(decision)

        policy = Policy(
            id="obs_test",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=5,
            window_ms=10_000,
        )
        limiter = FixedWindowLimiter(policy, storage, TestObserver())

        await limiter.check("test:key")

        assert len(decisions) == 1
        assert decisions[0].allowed is True

    async def test_observer_called_on_reject(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        """Observer.on_decision is called for rejected requests."""
        decisions: list[Decision] = []

        class TestObserver(NullObserver):
            def on_decision(self, policy_id: str, decision: Decision) -> None:
                decisions.append(decision)

        policy = Policy(
            id="obs_test",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=1,
            window_ms=10_000,
        )
        limiter = FixedWindowLimiter(policy, storage, TestObserver())

        await limiter.check("test:key")
        await limiter.check("test:key")

        assert len(decisions) == 2
        assert decisions[0].allowed is True
        assert decisions[1].allowed is False


# ── Factory integration ───────────────────────────────────────────


@pytest.mark.unit
class TestFixedWindowFactory:
    """Test that FixedWindow can be created via LimiterFactory."""

    async def test_factory_creates_fixed_window(
        self, clock: FakeClock, storage: MemoryStorage
    ) -> None:
        from app.core.limiter import LimiterFactory

        factory = LimiterFactory()
        factory.register(Algorithm.FIXED_WINDOW, FixedWindowLimiter)

        policy = Policy(
            id="factory_test",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=10,
            window_ms=5_000,
        )
        limiter = factory.create(policy, storage)

        assert isinstance(limiter, FixedWindowLimiter)
        decision = await limiter.check("test:key")
        assert decision.allowed is True

    def test_factory_rejects_unregistered(self) -> None:
        from app.core.limiter import LimiterFactory

        factory = LimiterFactory()
        policy = Policy(
            id="missing",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=10,
            window_ms=5_000,
        )
        with pytest.raises(ValueError, match="No limiter registered"):
            factory.create(policy, MemoryStorage(FakeClock()))
