"""Observer protocol for rate limiter events.

Implements the Observer pattern with all-no-op defaults. Concrete
implementations (e.g. Prometheus metrics) override the methods they
care about.

Must not import FastAPI, Redis, or Prometheus.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.models import Decision


@runtime_checkable
class Observer(Protocol):
    """Protocol for observing rate limiter events.

    All methods are no-ops by default (via the NullObserver
    implementation). The Prometheus metrics adapter (Phase 8)
    will implement this protocol to emit metrics.
    """

    def on_decision(self, policy_id: str, decision: Decision) -> None:
        """Called after every rate-limit decision.

        Args:
            policy_id: The policy that was evaluated.
            decision: The resulting decision.
        """
        ...

    def on_storage_op(self, op: str, seconds: float, ok: bool) -> None:
        """Called after every storage operation.

        Args:
            op: The operation name (e.g. "incr_expiring", "optimistic").
            seconds: Duration of the operation.
            ok: Whether the operation succeeded.
        """
        ...

    def on_txn_conflict(self, algorithm: str) -> None:
        """Called when an optimistic transaction conflicts (WATCH).

        Args:
            algorithm: The algorithm that experienced the conflict.
        """
        ...

    def on_txn_retry_exhausted(self, algorithm: str) -> None:
        """Called when optimistic retries are fully exhausted.

        Args:
            algorithm: The algorithm that exhausted retries.
        """
        ...

    def on_breaker_state(self, state: str) -> None:
        """Called when the circuit breaker changes state.

        Args:
            state: The new state ("closed", "open", "half_open").
        """
        ...


class NullObserver:
    """No-op observer implementation.

    Used as the default when no metrics/monitoring is configured.
    All methods do nothing.
    """

    def on_decision(self, policy_id: str, decision: Decision) -> None:
        pass

    def on_storage_op(self, op: str, seconds: float, ok: bool) -> None:
        pass

    def on_txn_conflict(self, algorithm: str) -> None:
        pass

    def on_txn_retry_exhausted(self, algorithm: str) -> None:
        pass

    def on_breaker_state(self, state: str) -> None:
        pass
