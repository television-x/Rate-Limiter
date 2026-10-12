"""Storage contract test suite.

Parametrized tests that verify the atomicity and correctness
guarantees of the Storage interface. Run against every backend
to ensure behavioral equivalence.

Phase 1: Memory store only.
Phase 4: Also parametrized with Redis store.
"""

from __future__ import annotations

import pytest

from app.core.clock import FakeClock
from app.core.storage.base import Storage
from app.core.storage.memory import MemoryStorage

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(params=["memory"])
async def store(request: pytest.FixtureRequest) -> Storage:
    """Parametrized storage fixture.

    Phase 1: memory only.
    Phase 4: add "redis" parameter.
    """
    if request.param == "memory":
        clock = FakeClock(start_ms=1_000_000_000_000)
        s = MemoryStorage(clock=clock)
        yield s  # type: ignore[misc]
        await s.close()
    else:
        pytest.skip(f"Unknown store: {request.param}")


@pytest.fixture
def fake_clock_for_store(store: Storage) -> FakeClock | None:
    """Get the FakeClock if the store uses one."""
    if isinstance(store, MemoryStorage):
        return store._clock  # type: ignore[return-value]
    return None


# ── Contract: now_ms ──────────────────────────────────────────────


@pytest.mark.contract
class TestNowMs:
    """Verify the clock source works."""

    async def test_returns_positive_int(self, store: Storage) -> None:
        result = await store.now_ms()
        assert isinstance(result, int)
        assert result > 0

    async def test_monotonic(self, store: Storage) -> None:
        t1 = await store.now_ms()
        t2 = await store.now_ms()
        assert t2 >= t1


# ── Contract: incr_expiring ──────────────────────────────────────


@pytest.mark.contract
class TestIncrExpiring:
    """Verify atomic increment with TTL."""

    async def test_creates_key_with_value(self, store: Storage) -> None:
        result = await store.incr_expiring("test:counter", by=1, ttl_ms=5000)
        assert result == 1

    async def test_increments_existing(self, store: Storage) -> None:
        await store.incr_expiring("test:counter", by=1, ttl_ms=5000)
        result = await store.incr_expiring("test:counter", by=1, ttl_ms=5000)
        assert result == 2

    async def test_increment_by_n(self, store: Storage) -> None:
        result = await store.incr_expiring("test:counter", by=5, ttl_ms=5000)
        assert result == 5

    async def test_expiry_removes_key(
        self, store: Storage, fake_clock_for_store: FakeClock | None
    ) -> None:
        """After TTL, the key should not exist (next incr starts from by)."""
        if fake_clock_for_store is None:
            pytest.skip("Expiry test requires FakeClock")

        await store.incr_expiring("test:expiry", by=1, ttl_ms=2000)
        fake_clock_for_store.advance(3000)  # Past TTL
        result = await store.incr_expiring("test:expiry", by=1, ttl_ms=2000)
        assert result == 1  # Key was expired, started fresh

    async def test_ttl_refreshed_on_each_incr(
        self, store: Storage, fake_clock_for_store: FakeClock | None
    ) -> None:
        """TTL is set unconditionally (idempotent) on every incr."""
        if fake_clock_for_store is None:
            pytest.skip("TTL test requires FakeClock")

        await store.incr_expiring("test:ttl", by=1, ttl_ms=3000)
        fake_clock_for_store.advance(2000)  # 2s in, not expired yet
        result = await store.incr_expiring("test:ttl", by=1, ttl_ms=3000)
        assert result == 2  # Still alive, TTL refreshed

        fake_clock_for_store.advance(2000)  # 4s total from last incr — not yet 3s from second incr
        # The TTL was refreshed at t=2000, so at t=4000 only 2s has passed since refresh
        result = await store.incr_expiring("test:ttl", by=1, ttl_ms=3000)
        assert result == 3  # Still alive due to TTL refresh


# ── Contract: incr_expiring_peek ─────────────────────────────────


@pytest.mark.contract
class TestIncrExpiringPeek:
    """Verify atomic increment + peek."""

    async def test_peek_nonexistent_returns_zero(self, store: Storage) -> None:
        new_count, peek_val = await store.incr_expiring_peek(
            "curr", by=1, ttl_ms=5000, peek_key="prev"
        )
        assert new_count == 1
        assert peek_val == 0

    async def test_peek_existing_returns_value(self, store: Storage) -> None:
        await store.incr_expiring("prev", by=10, ttl_ms=10000)
        new_count, peek_val = await store.incr_expiring_peek(
            "curr", by=1, ttl_ms=5000, peek_key="prev"
        )
        assert new_count == 1
        assert peek_val == 10

    async def test_multiple_increments_and_peeks(self, store: Storage) -> None:
        await store.incr_expiring("prev", by=42, ttl_ms=10000)
        await store.incr_expiring_peek("curr", by=3, ttl_ms=5000, peek_key="prev")
        new_count, peek_val = await store.incr_expiring_peek(
            "curr", by=2, ttl_ms=5000, peek_key="prev"
        )
        assert new_count == 5  # 3 + 2
        assert peek_val == 42


# ── Contract: decr ───────────────────────────────────────────────


@pytest.mark.contract
class TestDecr:
    """Verify atomic decrement."""

    async def test_decrement_existing(self, store: Storage) -> None:
        await store.incr_expiring("test:val", by=10, ttl_ms=10000)
        result = await store.decr("test:val", by=3)
        assert result == 7

    async def test_decrement_nonexistent_goes_negative(self, store: Storage) -> None:
        result = await store.decr("test:missing", by=1)
        assert result == -1

    async def test_decrement_to_zero(self, store: Storage) -> None:
        await store.incr_expiring("test:val", by=5, ttl_ms=10000)
        result = await store.decr("test:val", by=5)
        assert result == 0


# ── Contract: optimistic ─────────────────────────────────────────


@pytest.mark.contract
class TestOptimistic:
    """Verify optimistic transaction behavior."""

    async def test_commit_succeeds_when_no_conflict(self, store: Storage) -> None:
        """Transaction commits when watched keys are unchanged."""
        txn = await store.optimistic(["test:key"])
        val = await txn.get("test:key")
        assert val is None

        txn.set("test:key", "hello", px=5000)
        result = await txn.commit()
        assert result is True

    async def test_commit_fails_on_conflict(self, store: Storage) -> None:
        """Transaction fails when watched key is modified."""
        # Set up a key
        await store.incr_expiring("test:key", by=1, ttl_ms=10000)

        # Start transaction watching the key
        txn = await store.optimistic(["test:key"])
        _ = await txn.get("test:key")

        # Modify the key outside the transaction
        await store.incr_expiring("test:key", by=1, ttl_ms=10000)

        # Commit should fail
        txn.set("test:key", "should_not_persist")
        result = await txn.commit()
        assert result is False

    async def test_hset_and_hgetall(self, store: Storage) -> None:
        """Transaction can read and write hash keys."""
        txn = await store.optimistic(["test:hash"])
        data = await txn.hgetall("test:hash")
        assert data == {}

        txn.hset("test:hash", {"field1": "value1", "field2": "value2"})
        txn.pexpire("test:hash", 5000)
        assert await txn.commit() is True

        # Verify the data persisted
        txn2 = await store.optimistic(["test:hash"])
        data = await txn2.hgetall("test:hash")
        assert data == {"field1": "value1", "field2": "value2"}

    async def test_zadd_and_zrangebyscore(self, store: Storage) -> None:
        """Transaction can read and write sorted sets."""
        txn = await store.optimistic(["test:zset"])
        members = await txn.zrangebyscore("test:zset", 0, 100)
        assert members == []

        txn.zadd("test:zset", {"a": 10.0, "b": 20.0, "c": 30.0})
        txn.pexpire("test:zset", 5000)
        assert await txn.commit() is True

        # Read back
        txn2 = await store.optimistic(["test:zset"])
        members = await txn2.zrangebyscore("test:zset", 15, 100)
        assert set(members) == {"b", "c"}

    async def test_zremrangebyscore(self, store: Storage) -> None:
        """Transaction can remove sorted set members by score range."""
        # First, add some members
        txn = await store.optimistic(["test:zset2"])
        txn.zadd("test:zset2", {"old": 5.0, "mid": 15.0, "new": 25.0})
        assert await txn.commit() is True

        # Remove old entries
        txn2 = await store.optimistic(["test:zset2"])
        txn2.zremrangebyscore("test:zset2", 0, 10)
        assert await txn2.commit() is True

        # Verify
        txn3 = await store.optimistic(["test:zset2"])
        members = await txn3.zrangebyscore("test:zset2", 0, 100)
        assert set(members) == {"mid", "new"}

    async def test_conflict_injection_hook(self, store: Storage) -> None:
        """Conflict injection hook causes commits to fail."""
        if not isinstance(store, MemoryStorage):
            pytest.skip("Conflict injection only for memory store")

        fail_count = 0
        max_fails = 3

        def hook() -> bool:
            nonlocal fail_count
            if fail_count < max_fails:
                fail_count += 1
                return True
            return False

        store.set_conflict_hook(hook)

        # First 3 commits should fail
        for i in range(max_fails):
            txn = await store.optimistic(["test:hook"])
            txn.set("test:hook", f"attempt_{i}")
            result = await txn.commit()
            assert result is False, f"Attempt {i} should fail"

        # 4th commit should succeed
        txn = await store.optimistic(["test:hook"])
        txn.set("test:hook", "success")
        result = await txn.commit()
        assert result is True

        assert store.conflict_count == max_fails
        store.set_conflict_hook(None)


# ── Contract: delete ─────────────────────────────────────────────


@pytest.mark.contract
class TestDelete:
    """Verify key deletion."""

    async def test_delete_existing(self, store: Storage) -> None:
        await store.incr_expiring("test:del", by=1, ttl_ms=10000)
        assert await store.delete("test:del") is True

    async def test_delete_nonexistent(self, store: Storage) -> None:
        assert await store.delete("test:missing") is False

    async def test_key_gone_after_delete(self, store: Storage) -> None:
        await store.incr_expiring("test:del2", by=5, ttl_ms=10000)
        await store.delete("test:del2")
        # Next incr should start fresh
        result = await store.incr_expiring("test:del2", by=1, ttl_ms=10000)
        assert result == 1
