"""Bounded optimistic transaction retry with jittered backoff.

Phase 5: Used by Token Bucket and Sliding Window Log algorithms when
executing conditional decisions under optimistic concurrency (WATCH / version checks).

Must not import FastAPI, Redis, or Prometheus.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from app.core.errors import ContentionExhausted
from app.core.observer import Observer

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_MIN_JITTER_MS = 1
DEFAULT_MAX_JITTER_MS = 5


async def run_with_retry[T](
    fn: Callable[[int], Awaitable[tuple[bool, T]]],
    *,
    key: str,
    algorithm: str,
    observer: Observer,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    min_jitter_ms: int = DEFAULT_MIN_JITTER_MS,
    max_jitter_ms: int = DEFAULT_MAX_JITTER_MS,
) -> T:
    """Execute an optimistic transaction attempt with bounded retries and jittered backoff.

    Args:
        fn: A coroutine function accepting attempt number (0-indexed) and returning
            (committed: bool, result: T). If committed is True, result is returned.
            If committed is False, a conflict occurred and retry is triggered.
        key: The key being operated on.
        algorithm: String name of the algorithm (e.g. "token_bucket", "sliding_window_log").
        observer: Observer instance for event emission.
        max_attempts: Maximum retry attempts before raising ContentionExhausted.
        min_jitter_ms: Minimum jitter sleep duration in milliseconds.
        max_jitter_ms: Maximum jitter sleep duration in milliseconds.

    Returns:
        The result T if committed or admitted without write.

    Raises:
        ContentionExhausted: If max_attempts are exhausted due to conflicts.
    """
    for attempt in range(max_attempts):
        committed, result = await fn(attempt)
        if committed:
            return result

        # Conflict occurred on attempt
        observer.on_txn_conflict(algorithm)

        # If more attempts remain, backoff with jitter
        if attempt + 1 < max_attempts:
            jitter_ms = random.uniform(min_jitter_ms, max_jitter_ms)
            await asyncio.sleep(jitter_ms / 1000.0)

    # Retries exhausted
    observer.on_txn_retry_exhausted(algorithm)
    raise ContentionExhausted(key=key, attempts=max_attempts, algorithm=algorithm)
