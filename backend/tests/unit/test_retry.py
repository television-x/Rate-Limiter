"""Unit tests for bounded optimistic transaction retry mechanism (Phase 5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.algorithms.sliding_window_log import SlidingWindowLogLimiter
from app.core.algorithms.token_bucket import TokenBucketLimiter
from app.core.clock import FakeClock
from app.core.errors import ContentionExhausted
from app.core.models import Algorithm, DecisionReason, Policy
from app.core.observer import Observer
from app.core.storage.memory import MemoryStorage
from app.core.storage.retry import run_with_retry


@pytest.mark.unit
class TestRunWithRetry:
    """Tests for the generic run_with_retry function."""

    async def test_succeeds_first_attempt(self) -> None:
        """First attempt commits successfully; no conflict emitted."""
        observer = MagicMock(spec=Observer)
        calls = 0

        async def op(attempt: int) -> tuple[bool, str]:
            nonlocal calls
            calls += 1
            return True, "success"

        result = await run_with_retry(
            op,
            key="test_key",
            algorithm="test_algo",
            observer=observer,
            max_attempts=5,
            min_jitter_ms=0,
            max_jitter_ms=0,
        )

        assert result == "success"
        assert calls == 1
        observer.on_txn_conflict.assert_not_called()
        observer.on_txn_retry_exhausted.assert_not_called()

    async def test_retries_on_conflict_and_succeeds(self) -> None:
        """Retries on conflict and succeeds when attempt commits."""
        observer = MagicMock(spec=Observer)
        attempts_seen = []

        async def op(attempt: int) -> tuple[bool, str]:
            attempts_seen.append(attempt)
            if attempt < 2:
                return False, "conflict"
            return True, "eventual_success"

        result = await run_with_retry(
            op,
            key="test_key",
            algorithm="token_bucket",
            observer=observer,
            max_attempts=5,
            min_jitter_ms=0,
            max_jitter_ms=0,
        )

        assert result == "eventual_success"
        assert attempts_seen == [0, 1, 2]
        assert observer.on_txn_conflict.call_count == 2
        observer.on_txn_conflict.assert_called_with("token_bucket")
        observer.on_txn_retry_exhausted.assert_not_called()

    async def test_exhaustion_raises_contention_exhausted(self) -> None:
        """Exhausting all attempts raises ContentionExhausted."""
        observer = MagicMock(spec=Observer)
        attempts_seen = []

        async def op(attempt: int) -> tuple[bool, str]:
            attempts_seen.append(attempt)
            return False, "conflict"

        with pytest.raises(ContentionExhausted) as exc_info:
            await run_with_retry(
                op,
                key="test_key",
                algorithm="sliding_window_log",
                observer=observer,
                max_attempts=5,
                min_jitter_ms=0,
                max_jitter_ms=0,
            )

        assert exc_info.value.algorithm == "sliding_window_log"
        assert exc_info.value.attempts == 5
        assert attempts_seen == [0, 1, 2, 3, 4]
        assert observer.on_txn_conflict.call_count == 5
        observer.on_txn_retry_exhausted.assert_called_once_with("sliding_window_log")


@pytest.mark.unit
class TestAlgorithmConflictRetry:
    """Tests deterministic conflict hook integration on limiters."""

    async def test_token_bucket_conflict_retry_and_success(self) -> None:
        """Token bucket retries on conflict and succeeds once commit goes through."""
        clock = FakeClock(start_ms=1_000_000_000_000)
        store = MemoryStorage(clock=clock)
        observer = MagicMock(spec=Observer)

        policy = Policy(
            id="p-tb-retry",
            algorithm=Algorithm.TOKEN_BUCKET,
            capacity=10,
            refill_per_sec=1.0,
        )
        limiter = TokenBucketLimiter(policy, store, observer, max_retries=5)
        key = "rl:v1:{tb|user:test}:token_bucket"

        # Fail the first 2 commits, then succeed
        fail_counter = 0

        def hook() -> bool:
            nonlocal fail_counter
            if fail_counter < 2:
                fail_counter += 1
                return True
            return False

        store.set_conflict_hook(hook)

        decision = await limiter.check(key, cost=1)
        assert decision.allowed is True
        assert decision.remaining == 9
        assert decision.reason == DecisionReason.ALLOWED
        assert observer.on_txn_conflict.call_count == 2
        observer.on_txn_retry_exhausted.assert_not_called()

    async def test_token_bucket_retry_exhaustion_raises(self) -> None:
        """Token bucket raises ContentionExhausted after 5 failed attempts."""
        clock = FakeClock(start_ms=1_000_000_000_000)
        store = MemoryStorage(clock=clock)
        observer = MagicMock(spec=Observer)

        policy = Policy(
            id="p-tb-exhaust",
            algorithm=Algorithm.TOKEN_BUCKET,
            capacity=10,
            refill_per_sec=1.0,
        )
        limiter = TokenBucketLimiter(policy, store, observer, max_retries=5)
        key = "rl:v1:{tb|user:test}:token_bucket"

        # Always fail commit
        store.set_conflict_hook(lambda: True)

        with pytest.raises(ContentionExhausted) as exc_info:
            await limiter.check(key, cost=1)

        assert exc_info.value.algorithm == Algorithm.TOKEN_BUCKET.value
        assert exc_info.value.attempts == 5
        assert observer.on_txn_conflict.call_count == 5
        observer.on_txn_retry_exhausted.assert_called_once_with(Algorithm.TOKEN_BUCKET.value)

    async def test_sliding_window_log_conflict_retry_and_success(self) -> None:
        """Sliding window log retries on conflict and succeeds once commit goes through."""
        clock = FakeClock(start_ms=1_000_000_000_000)
        store = MemoryStorage(clock=clock)
        observer = MagicMock(spec=Observer)

        policy = Policy(
            id="p-swl-retry",
            algorithm=Algorithm.SLIDING_WINDOW_LOG,
            limit=10,
            window_ms=60_000,
        )
        limiter = SlidingWindowLogLimiter(policy, store, observer, max_retries=5)
        key = "rl:v1:{swl|user:test}:sliding_log"

        fail_counter = 0

        def hook() -> bool:
            nonlocal fail_counter
            if fail_counter < 2:
                fail_counter += 1
                return True
            return False

        store.set_conflict_hook(hook)

        decision = await limiter.check(key, cost=1)
        assert decision.allowed is True
        assert decision.remaining == 9
        assert decision.reason == DecisionReason.ALLOWED
        assert observer.on_txn_conflict.call_count == 2
        observer.on_txn_retry_exhausted.assert_not_called()

    async def test_sliding_window_log_retry_exhaustion_raises(self) -> None:
        """Sliding window log raises ContentionExhausted after 5 failed attempts."""
        clock = FakeClock(start_ms=1_000_000_000_000)
        store = MemoryStorage(clock=clock)
        observer = MagicMock(spec=Observer)

        policy = Policy(
            id="p-swl-exhaust",
            algorithm=Algorithm.SLIDING_WINDOW_LOG,
            limit=10,
            window_ms=60_000,
        )
        limiter = SlidingWindowLogLimiter(policy, store, observer, max_retries=5)
        key = "rl:v1:{swl|user:test}:sliding_log"

        store.set_conflict_hook(lambda: True)

        with pytest.raises(ContentionExhausted) as exc_info:
            await limiter.check(key, cost=1)

        assert exc_info.value.algorithm == Algorithm.SLIDING_WINDOW_LOG.value
        assert exc_info.value.attempts == 5
        assert observer.on_txn_conflict.call_count == 5
        observer.on_txn_retry_exhausted.assert_called_once_with(Algorithm.SLIDING_WINDOW_LOG.value)
