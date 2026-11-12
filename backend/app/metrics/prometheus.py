"""Prometheus metrics collector and observer implementation.

Per AGENTS.md §Phase 8:
- Low-cardinality Prometheus metrics for all subsystems
- Implements core.observer.Observer protocol
- Exposes /metrics endpoint
- Strict adherence to label rules: never use user IDs, IPs, request IDs, or raw paths
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.core.models import Decision
from app.core.observer import Observer


class MetricsRegistry:
    """Holds all Prometheus metric definitions bound to a specific CollectorRegistry."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)

        # 1. Decision metrics
        self.decisions_total = Counter(
            "rl_decisions_total",
            "Total rate limiter decisions evaluated",
            ["policy", "algorithm", "decision", "reason"],
            registry=self.registry,
        )
        self.decision_duration_seconds = Histogram(
            "rl_decision_duration_seconds",
            "Duration of rate limiter decision calculation in seconds",
            ["algorithm"],
            registry=self.registry,
        )

        # 2. HTTP metrics
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds",
            ["method", "route_template", "status_class"],
            registry=self.registry,
        )

        # 3. Storage metrics
        self.storage_op_duration_seconds = Histogram(
            "rl_storage_op_duration_seconds",
            "Storage operation latency in seconds",
            ["op"],
            registry=self.registry,
        )
        self.storage_errors_total = Counter(
            "rl_storage_errors_total",
            "Total storage operation errors",
            ["op", "error_type"],
            registry=self.registry,
        )
        self.storage_up = Gauge(
            "rl_storage_up",
            "Storage backend reachability status (1=up, 0=down)",
            registry=self.registry,
        )

        # 4. Optimistic transaction metrics
        self.txn_conflicts_total = Counter(
            "rl_txn_conflicts_total",
            "Optimistic transaction conflict count",
            ["algorithm"],
            registry=self.registry,
        )
        self.txn_attempts = Histogram(
            "rl_txn_attempts",
            "Distribution of transaction retry attempts",
            ["algorithm"],
            buckets=[1, 2, 3, 4, 5, 6],
            registry=self.registry,
        )
        self.txn_retry_exhausted_total = Counter(
            "rl_txn_retry_exhausted_total",
            "Total optimistic transaction retries exhausted",
            ["algorithm"],
            registry=self.registry,
        )

        # 5. Reliability & Breaker metrics
        self.failure_policy_applied_total = Counter(
            "rl_failure_policy_applied_total",
            "Total failure policies triggered due to backend failures",
            ["mode"],
            registry=self.registry,
        )
        self.breaker_state = Gauge(
            "rl_breaker_state",
            "Circuit breaker state gauge (1 for active state, 0 otherwise)",
            ["state"],
            registry=self.registry,
        )

        # 6. Sliding window log metrics
        self.log_entries_seen = Histogram(
            "rl_log_entries_seen",
            "Count of log entries scanned per evaluation in sliding window log",
            ["algorithm"],
            registry=self.registry,
        )

        # Initialize gauges
        self.storage_up.set(1.0)
        for s in ("closed", "open", "half_open"):
            self.breaker_state.labels(state=s).set(1.0 if s == "closed" else 0.0)


class PrometheusMetricsObserver(Observer):
    """Observer that records rate limiter and system events into Prometheus metrics."""

    def __init__(self, metrics: MetricsRegistry | None = None) -> None:
        self.metrics = metrics or MetricsRegistry()

    @property
    def registry(self) -> CollectorRegistry:
        """Access the underlying CollectorRegistry."""
        return self.metrics.registry

    def on_decision(
        self,
        policy_id: str,
        decision: Decision,
        duration_seconds: float = 0.0,
    ) -> None:
        """Record a rate-limiting decision."""
        decision_str = "allowed" if decision.allowed else "denied"
        self.metrics.decisions_total.labels(
            policy=policy_id,
            algorithm=decision.algorithm.value,
            decision=decision_str,
            reason=decision.reason.value,
        ).inc()

        if duration_seconds > 0:
            self.metrics.decision_duration_seconds.labels(
                algorithm=decision.algorithm.value
            ).observe(duration_seconds)

    def on_storage_op(
        self,
        op: str,
        seconds: float,
        ok: bool,
        error_type: str = "",
    ) -> None:
        """Record a storage operation duration and error count if applicable."""
        self.metrics.storage_op_duration_seconds.labels(op=op).observe(seconds)
        if not ok:
            self.metrics.storage_errors_total.labels(
                op=op,
                error_type=error_type or "unknown",
            ).inc()

    def on_txn_conflict(self, algorithm: str) -> None:
        """Record an optimistic concurrency transaction conflict."""
        self.metrics.txn_conflicts_total.labels(algorithm=algorithm).inc()

    def on_txn_attempts(self, algorithm: str, attempts: int) -> None:
        """Record retry attempts for an optimistic transaction."""
        self.metrics.txn_attempts.labels(algorithm=algorithm).observe(attempts)

    def on_txn_retry_exhausted(self, algorithm: str) -> None:
        """Record an exhausted optimistic transaction."""
        self.metrics.txn_retry_exhausted_total.labels(algorithm=algorithm).inc()

    def on_breaker_state(self, state: str) -> None:
        """Update circuit breaker state gauge."""
        for s in ("closed", "open", "half_open"):
            self.metrics.breaker_state.labels(state=s).set(1.0 if s == state else 0.0)

    def on_failure_policy(self, mode: str) -> None:
        """Record an application of a fallback or failure policy."""
        self.metrics.failure_policy_applied_total.labels(mode=mode).inc()

    def on_storage_status(self, up: bool) -> None:
        """Update storage health indicator."""
        self.metrics.storage_up.set(1.0 if up else 0.0)

    def on_log_entries_seen(self, count: int) -> None:
        """Record number of entries seen in sliding window log."""
        self.metrics.log_entries_seen.labels(algorithm="sliding_log").observe(count)

    def on_http_request(
        self,
        method: str,
        route_template: str,
        status_class: str,
        duration_seconds: float,
    ) -> None:
        """Record HTTP request latency and status."""
        self.metrics.http_request_duration_seconds.labels(
            method=method,
            route_template=route_template,
            status_class=status_class,
        ).observe(duration_seconds)


# Global default observer and router for application use
default_metrics = MetricsRegistry()
default_observer = PrometheusMetricsObserver(default_metrics)

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics_endpoint() -> Response:
    """Scrape endpoint exposing Prometheus metrics in OpenMetrics/Prometheus text format."""
    content = generate_latest(default_metrics.registry)
    return Response(content=content, media_type=CONTENT_TYPE_LATEST)
