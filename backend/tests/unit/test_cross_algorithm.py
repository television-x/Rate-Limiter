"""Cross-algorithm comparison test suite.

Simulates identical traffic traces across all four rate-limiting algorithms
and compares admitted vs denied requests. Serves as behavioral proof and
data foundation for docs/algorithms.md.
"""

from __future__ import annotations

import pytest

from app.core.algorithms.fixed_window import FixedWindowLimiter
from app.core.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from app.core.algorithms.sliding_window_log import SlidingWindowLogLimiter
from app.core.algorithms.token_bucket import TokenBucketLimiter
from app.core.clock import FakeClock
from app.core.models import Algorithm, Policy
from app.core.storage.memory import MemoryStorage


@pytest.mark.unit
class TestCrossAlgorithmComparison:
    """Comparison of algorithm behaviors under identical traffic traces."""

    async def test_boundary_burst_traffic_trace(self) -> None:
        """Trace: send 10 requests at end of window 1, then 10 at start of window 2.

        Window size = 10_000ms, Limit = 10.
        Fixed Window allows all 20 (the classic 2L boundary burst anomaly).
        Sliding Log admits exactly 10 (true sliding window enforcement).
        Sliding Counter admits ~10-11 (close approximation).
        Token Bucket (C=10, r=1/s) admits 10 initially, then only ~1 refilled token.
        """
        clock = FakeClock(start_ms=9_500)
        store = MemoryStorage(clock=clock)

        fw_policy = Policy(id="p-fw", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=10_000)
        sl_policy = Policy(
            id="p-sl", algorithm=Algorithm.SLIDING_WINDOW_LOG, limit=10, window_ms=10_000
        )
        sc_policy = Policy(
            id="p-sc", algorithm=Algorithm.SLIDING_WINDOW_COUNTER, limit=10, window_ms=10_000
        )
        tb_policy = Policy(
            id="p-tb", algorithm=Algorithm.TOKEN_BUCKET, capacity=10, refill_per_sec=1
        )

        fw = FixedWindowLimiter(fw_policy, store)
        sl = SlidingWindowLogLimiter(sl_policy, store)
        sc = SlidingWindowCounterLimiter(sc_policy, store)
        tb = TokenBucketLimiter(tb_policy, store)

        results = {"fw": 0, "sl": 0, "sc": 0, "tb": 0}

        # Step 1: Burst 10 requests at T = 9,500ms (end of window 0)
        for _ in range(10):
            if (await fw.check("k:fw")).allowed:
                results["fw"] += 1
            if (await sl.check("k:sl")).allowed:
                results["sl"] += 1
            if (await sc.check("k:sc")).allowed:
                results["sc"] += 1
            if (await tb.check("k:tb")).allowed:
                results["tb"] += 1

        # All 4 allow the initial 10 requests
        assert results["fw"] == 10
        assert results["sl"] == 10
        assert results["sc"] == 10
        assert results["tb"] == 10

        # Step 2: Step forward 600ms to T = 10_100ms (crosses fixed window boundary into window 1)
        clock.set(10_100)

        # Step 3: Burst another 10 requests immediately at T = 10_100ms
        for _ in range(10):
            if (await fw.check("k:fw")).allowed:
                results["fw"] += 1
            if (await sl.check("k:sl")).allowed:
                results["sl"] += 1
            if (await sc.check("k:sc")).allowed:
                results["sc"] += 1
            if (await tb.check("k:tb")).allowed:
                results["tb"] += 1

        # Fixed Window allowed 20/20 (exhibits 2L boundary burst!)
        assert results["fw"] == 20

        # Sliding Window Log allowed 10/20 (exact sliding window: 0 allowed across boundary)
        assert results["sl"] == 10

        # Sliding Window Counter allowed ~10 (at 10_100, elapsed=100ms, weight=0.99, prev=10 -> prev contrib=9, curr allowed=1)
        assert results["sc"] == 11

        # Token Bucket refilled min(10, 0 + 0.6s * 1/s) = 0 tokens -> 10/20 allowed
        assert results["tb"] == 10

        await store.close()
