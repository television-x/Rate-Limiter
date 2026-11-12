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
    implements this protocol to emit metrics.
    """

    def on_decision(self, policy_id: str, decision: Decision) -> None:
        """Called after every rate-limit decision."""
        ...

    def on_storage_op(self, op: str, seconds: float, ok: bool) -> None:
        """Called after every storage operation."""
        ...

    def on_txn_conflict(self, algorithm: str) -> None:
        """Called when an optimistic transaction conflicts (WATCH)."""
        ...

    def on_txn_retry_exhausted(self, algorithm: str) -> None:
        """Called when optimistic retries are fully exhausted."""
        ...

    def on_breaker_state(self, state: str) -> None:
        """Called when the circuit breaker changes state."""
        ...


class NullObserver:
    """No-op observer implementation.

    Used as the default when no metrics/monitoring is configured.
    All methods do nothing.
    """

    def on_decision(
        self, policy_id: str, decision: Decision, duration_seconds: float = 0.0
    ) -> None:
        pass

    def on_storage_op(self, op: str, seconds: float, ok: bool, error_type: str = "") -> None:
        pass

    def on_txn_conflict(self, algorithm: str) -> None:
        pass

    def on_txn_attempts(self, algorithm: str, attempts: int) -> None:
        pass

    def on_txn_retry_exhausted(self, algorithm: str) -> None:
        pass

    def on_breaker_state(self, state: str) -> None:
        pass

    def on_failure_policy(self, mode: str) -> None:
        pass

    def on_storage_status(self, up: bool) -> None:
        pass

    def on_log_entries_seen(self, count: int) -> None:
        pass

    def on_http_request(
        self, method: str, route_template: str, status_class: str, duration_seconds: float
    ) -> None:
        pass
