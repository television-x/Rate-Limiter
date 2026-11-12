"""Unit tests for Prometheus metrics observer and label allowlist.

Per AGENTS.md §Phase 8:
- Low-cardinality Prometheus metrics for all subsystems
- Label allowlist test that fails on unknown labels
- Verifies all 12 required metrics
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.core.models import Accuracy, Algorithm, Decision, DecisionReason
from app.metrics.prometheus import MetricsRegistry, PrometheusMetricsObserver

pytestmark = pytest.mark.unit

# Strict label allowlist per AGENTS.md §Phase 8
ALLOWED_METRIC_LABELS: dict[str, set[str]] = {
    "rl_decisions_total": {"policy", "algorithm", "decision", "reason"},
    "rl_decision_duration_seconds": {"algorithm", "le"},
    "http_request_duration_seconds": {"method", "route_template", "status_class", "le"},
    "rl_storage_op_duration_seconds": {"op", "le"},
    "rl_storage_errors_total": {"op", "error_type"},
    "rl_txn_conflicts_total": {"algorithm"},
    "rl_txn_attempts": {"algorithm", "le"},
    "rl_txn_retry_exhausted_total": {"algorithm"},
    "rl_failure_policy_applied_total": {"mode"},
    "rl_breaker_state": {"state"},
    "rl_storage_up": set(),
    "rl_log_entries_seen": {"algorithm", "le"},
}

FORBIDDEN_LABEL_PATTERNS = {"user", "ip", "client", "request_id", "path"}


class TestMetricsRegistryLabels:
    """Validate label constraints and cardinality rules."""

    def test_all_metrics_use_strictly_allowed_labels(self) -> None:
        registry = CollectorRegistry()
        _ = MetricsRegistry(registry=registry)

        # Inspect all registered metric collectors and samples
        for metric in registry.collect():
            for sample in metric.samples:
                sample_name = sample.name
                if sample_name.endswith("_created"):
                    continue

                # Strip histogram suffixes
                base_name = sample_name
                for suffix in ("_bucket", "_count", "_sum"):
                    if base_name.endswith(suffix):
                        base_name = base_name[: -len(suffix)]

                # For counter with _total
                if (
                    base_name not in ALLOWED_METRIC_LABELS
                    and f"{base_name}_total" in ALLOWED_METRIC_LABELS
                ):
                    base_name = f"{base_name}_total"

                assert base_name in ALLOWED_METRIC_LABELS, (
                    f"Unexpected metric '{sample_name}' (base '{base_name}') not in specification"
                )

                allowed_labels = ALLOWED_METRIC_LABELS[base_name]
                for label_name in sample.labels:
                    assert label_name in allowed_labels, (
                        f"Metric '{base_name}' uses disallowed label '{label_name}'"
                    )

    def test_no_high_cardinality_labels_exist(self) -> None:
        registry = CollectorRegistry()
        _ = MetricsRegistry(registry=registry)

        for metric in registry.collect():
            for sample in metric.samples:
                for label_name in sample.labels:
                    lower = label_name.lower()
                    for forbidden in FORBIDDEN_LABEL_PATTERNS:
                        if forbidden in lower and lower != "route_template":
                            pytest.fail(
                                f"High-cardinality label pattern '{forbidden}' found in label '{label_name}' on metric '{metric.name}'"
                            )


class TestMetricsObserverEmission:
    """Test emission of all 12 metric families through the observer."""

    def test_all_metrics_emitted(self) -> None:
        registry = CollectorRegistry()
        metrics = MetricsRegistry(registry=registry)
        observer = PrometheusMetricsObserver(metrics)

        # 1. Decision + duration
        decision = Decision(
            allowed=True,
            limit=10,
            remaining=9,
            retry_after_ms=None,
            reset_after_ms=1000,
            algorithm=Algorithm.FIXED_WINDOW,
            reason=DecisionReason.ALLOWED,
            accuracy=Accuracy.EXACT,
        )
        observer.on_decision("default", decision, duration_seconds=0.005)

        # 2. HTTP request
        observer.on_http_request("GET", "/api/v1/demo/ping", "2xx", 0.012)

        # 3. Storage op + storage error
        observer.on_storage_op("incr_expiring", 0.002, ok=True)
        observer.on_storage_op("incr_expiring", 0.003, ok=False, error_type="ConnectionError")

        # 4. Storage up
        observer.on_storage_status(True)

        # 5. Optimistic transactions
        observer.on_txn_conflict("token_bucket")
        observer.on_txn_attempts("token_bucket", 3)
        observer.on_txn_retry_exhausted("token_bucket")

        # 6. Failure policy & breaker state
        observer.on_failure_policy("local_fallback")
        observer.on_breaker_state("open")

        # 7. Log entries seen
        observer.on_log_entries_seen(42)

        # Scrape and assert all 12 metric names are present in output
        output = generate_latest(registry).decode("utf-8")

        for expected_metric in ALLOWED_METRIC_LABELS:
            assert expected_metric in output, (
                f"Metric '{expected_metric}' missing from Prometheus scrape output"
            )
