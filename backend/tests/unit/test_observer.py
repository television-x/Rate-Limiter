"""Unit tests for the Observer protocol.

Verify that NullObserver is a valid implementation and that the
protocol is runtime-checkable.
"""

from __future__ import annotations

import pytest

from app.core.models import Algorithm, Decision, DecisionReason
from app.core.observer import NullObserver, Observer


@pytest.mark.unit
class TestNullObserver:
    """Verify NullObserver as a valid no-op implementation."""

    def test_implements_protocol(self) -> None:
        obs = NullObserver()
        assert isinstance(obs, Observer)

    def test_on_decision_is_noop(self) -> None:
        obs = NullObserver()
        decision = Decision(
            allowed=True,
            limit=100,
            remaining=99,
            retry_after_ms=None,
            reset_after_ms=500,
            algorithm=Algorithm.FIXED_WINDOW,
            reason=DecisionReason.ALLOWED,
        )
        # Should not raise
        obs.on_decision("test_policy", decision)

    def test_on_storage_op_is_noop(self) -> None:
        obs = NullObserver()
        obs.on_storage_op("incr_expiring", 0.001, True)

    def test_on_txn_conflict_is_noop(self) -> None:
        obs = NullObserver()
        obs.on_txn_conflict("token_bucket")

    def test_on_txn_retry_exhausted_is_noop(self) -> None:
        obs = NullObserver()
        obs.on_txn_retry_exhausted("sliding_window_log")

    def test_on_breaker_state_is_noop(self) -> None:
        obs = NullObserver()
        obs.on_breaker_state("open")


@pytest.mark.unit
class TestObserverProtocol:
    """Verify the Observer protocol is usable as a type."""

    def test_custom_observer_satisfies_protocol(self) -> None:
        """A class with the right methods satisfies the protocol."""

        class MyObserver:
            def __init__(self) -> None:
                self.decisions: list[Decision] = []

            def on_decision(self, policy_id: str, decision: Decision) -> None:
                self.decisions.append(decision)

            def on_storage_op(self, op: str, seconds: float, ok: bool) -> None:
                pass

            def on_txn_conflict(self, algorithm: str) -> None:
                pass

            def on_txn_retry_exhausted(self, algorithm: str) -> None:
                pass

            def on_breaker_state(self, state: str) -> None:
                pass

        obs = MyObserver()
        assert isinstance(obs, Observer)
