"""Unit tests for policy overrides and cross-instance propagation.

Verify bounded ranges, demo policy restrictions, and 2-second cache synchronization.
"""

from __future__ import annotations

import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidPolicy
from app.core.models import Algorithm, Policy
from app.core.storage.memory import MemoryStorage
from app.policies import (
    PolicyForbiddenError,
    PolicyRegistry,
)


@pytest.mark.unit
class TestPolicyOverrides:
    """Tests for PolicyRegistry dynamic runtime overrides."""

    def test_non_demo_policy_override_rejected(self) -> None:
        p_system = Policy(
            id="internal_billing",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=100,
            window_ms=60000,
        )
        registry = PolicyRegistry([p_system])

        p_updated = Policy(
            id="internal_billing",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=200,
            window_ms=60000,
        )

        with pytest.raises(PolicyForbiddenError, match="not a demo policy"):
            import asyncio

            asyncio.run(registry.set_override(p_updated))

    def test_unknown_policy_override_rejected(self) -> None:
        registry = PolicyRegistry()
        p_unknown = Policy(
            id="default",  # is demo id, but not in registry
            algorithm=Algorithm.FIXED_WINDOW,
            limit=50,
            window_ms=60000,
        )
        with pytest.raises(KeyError, match="does not exist"):
            import asyncio

            asyncio.run(registry.set_override(p_unknown))

    def test_out_of_bounds_policy_override_rejected(self) -> None:
        p_default = Policy(
            id="default",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=60,
            window_ms=60000,
        )
        registry = PolicyRegistry([p_default])

        # limit = 0 (out of bounds, min 1)
        with pytest.raises(InvalidPolicy, match="limit must be a positive integer"):
            Policy(
                id="default",
                algorithm=Algorithm.FIXED_WINDOW,
                limit=0,
                window_ms=60000,
            )

        # limit = 20000 (exceeds max 10000)
        p_too_high = Policy(
            id="default",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=20000,
            window_ms=60000,
        )
        with pytest.raises(ValueError, match="limit must be between 1 and 10000"):
            import asyncio

            asyncio.run(registry.set_override(p_too_high))

    @pytest.mark.asyncio
    async def test_overrides_propagate_across_two_instances(self) -> None:
        clock = FakeClock()
        storage = MemoryStorage(clock=clock)

        base_policy = Policy(
            id="fixed_window",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=10,
            window_ms=10000,
        )

        # Instance 1 and Instance 2 share same base policy and storage
        reg1 = PolicyRegistry([base_policy], cache_ttl_seconds=0.1)
        reg2 = PolicyRegistry([base_policy], cache_ttl_seconds=0.1)

        assert reg1.get("fixed_window").limit == 10
        assert reg2.get("fixed_window").limit == 10

        # Instance 1 updates the limit to 50
        updated = Policy(
            id="fixed_window",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=50,
            window_ms=10000,
        )
        await reg1.set_override(updated, storage=storage)

        # Instance 1 reflects it immediately
        assert reg1.get("fixed_window").limit == 50

        # Instance 2 still has cached value before cache TTL
        assert (await reg2.get_effective("fixed_window", storage=None)).limit == 10

        # Fast forward time to expire 0.1s cache
        import asyncio

        await asyncio.sleep(0.15)

        # Instance 2 queries with storage -> syncs and gets 50
        eff2 = await reg2.get_effective("fixed_window", storage=storage)
        assert eff2.limit == 50
        assert reg2.get("fixed_window").limit == 50
