"""Unit tests for core domain models.

Tests validation rules for Policy, Decision, and Subject.
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidPolicy
from app.core.models import (
    Accuracy,
    Algorithm,
    ContentionMode,
    Decision,
    DecisionReason,
    FailureMode,
    IdentityKind,
    Policy,
    Subject,
)

# ── Policy validation ─────────────────────────────────────────────


@pytest.mark.unit
class TestPolicyValidation:
    """Validate Policy construction rules per AGENTS.md §3.1."""

    def test_valid_fixed_window(self) -> None:
        p = Policy(id="fw", algorithm=Algorithm.FIXED_WINDOW, limit=100, window_ms=1000)
        assert p.limit == 100
        assert p.window_ms == 1000

    def test_valid_sliding_log(self) -> None:
        p = Policy(id="sl", algorithm=Algorithm.SLIDING_WINDOW_LOG, limit=50, window_ms=5000)
        assert p.algorithm == Algorithm.SLIDING_WINDOW_LOG

    def test_valid_sliding_counter(self) -> None:
        p = Policy(id="sc", algorithm=Algorithm.SLIDING_WINDOW_COUNTER, limit=200, window_ms=60000)
        assert p.cost_default == 1

    def test_valid_token_bucket(self) -> None:
        p = Policy(
            id="tb",
            algorithm=Algorithm.TOKEN_BUCKET,
            capacity=100,
            refill_per_sec=10.0,
        )
        assert p.capacity == 100
        assert p.refill_per_sec == 10.0

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(InvalidPolicy, match="id must be non-empty"):
            Policy(id="", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=1000)

    def test_negative_limit_rejected(self) -> None:
        with pytest.raises(InvalidPolicy, match="limit must be a positive"):
            Policy(id="bad", algorithm=Algorithm.FIXED_WINDOW, limit=-1, window_ms=1000)

    def test_zero_limit_rejected(self) -> None:
        with pytest.raises(InvalidPolicy, match="limit must be a positive"):
            Policy(id="bad", algorithm=Algorithm.FIXED_WINDOW, limit=0, window_ms=1000)

    def test_window_ms_too_small(self) -> None:
        with pytest.raises(InvalidPolicy, match="window_ms must be >= 1000"):
            Policy(id="bad", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=999)

    def test_window_ms_at_minimum(self) -> None:
        p = Policy(id="ok", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=1000)
        assert p.window_ms == 1000

    def test_cost_default_zero_rejected(self) -> None:
        with pytest.raises(InvalidPolicy, match="cost_default must be >= 1"):
            Policy(
                id="bad",
                algorithm=Algorithm.FIXED_WINDOW,
                limit=10,
                window_ms=1000,
                cost_default=0,
            )

    def test_token_bucket_zero_capacity_rejected(self) -> None:
        with pytest.raises(InvalidPolicy, match="capacity must be a positive"):
            Policy(
                id="bad",
                algorithm=Algorithm.TOKEN_BUCKET,
                capacity=0,
                refill_per_sec=1.0,
            )

    def test_token_bucket_zero_refill_rejected(self) -> None:
        with pytest.raises(InvalidPolicy, match="refill_per_sec must be > 0"):
            Policy(
                id="bad",
                algorithm=Algorithm.TOKEN_BUCKET,
                capacity=10,
                refill_per_sec=0,
            )

    def test_token_bucket_negative_refill_rejected(self) -> None:
        with pytest.raises(InvalidPolicy, match="refill_per_sec must be > 0"):
            Policy(
                id="bad",
                algorithm=Algorithm.TOKEN_BUCKET,
                capacity=10,
                refill_per_sec=-1.0,
            )

    def test_token_bucket_capacity_less_than_cost(self) -> None:
        with pytest.raises(InvalidPolicy, match="capacity must be >= cost_default"):
            Policy(
                id="bad",
                algorithm=Algorithm.TOKEN_BUCKET,
                capacity=2,
                refill_per_sec=1.0,
                cost_default=5,
            )

    def test_failure_mode_defaults_to_open(self) -> None:
        p = Policy(id="d", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=1000)
        assert p.on_failure == FailureMode.OPEN

    def test_contention_mode_defaults_to_reject(self) -> None:
        p = Policy(id="d", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=1000)
        assert p.on_contention == ContentionMode.REJECT

    def test_scope_defaults_to_ip(self) -> None:
        p = Policy(id="d", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=1000)
        assert p.scope == frozenset({IdentityKind.IP})

    def test_custom_scope(self) -> None:
        p = Policy(
            id="multi",
            algorithm=Algorithm.FIXED_WINDOW,
            limit=10,
            window_ms=1000,
            scope=frozenset({IdentityKind.USER, IdentityKind.ROUTE}),
        )
        assert IdentityKind.USER in p.scope
        assert IdentityKind.ROUTE in p.scope

    def test_policy_is_frozen(self) -> None:
        p = Policy(id="f", algorithm=Algorithm.FIXED_WINDOW, limit=10, window_ms=1000)
        with pytest.raises(AttributeError):
            p.limit = 20  # type: ignore[misc]


# ── Decision ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestDecision:
    """Verify Decision data model."""

    def test_allowed_decision(self) -> None:
        d = Decision(
            allowed=True,
            limit=100,
            remaining=99,
            retry_after_ms=None,
            reset_after_ms=500,
            algorithm=Algorithm.FIXED_WINDOW,
            reason=DecisionReason.ALLOWED,
        )
        assert d.allowed is True
        assert d.remaining == 99
        assert d.degraded is False
        assert d.accuracy == Accuracy.EXACT

    def test_denied_decision(self) -> None:
        d = Decision(
            allowed=False,
            limit=100,
            remaining=0,
            retry_after_ms=1000,
            reset_after_ms=1000,
            algorithm=Algorithm.FIXED_WINDOW,
            reason=DecisionReason.LIMIT_EXCEEDED,
        )
        assert d.allowed is False
        assert d.retry_after_ms == 1000

    def test_degraded_decision(self) -> None:
        d = Decision(
            allowed=True,
            limit=100,
            remaining=None,
            retry_after_ms=None,
            reset_after_ms=None,
            algorithm=Algorithm.FIXED_WINDOW,
            reason=DecisionReason.DEGRADED_ALLOWED,
            degraded=True,
        )
        assert d.degraded is True
        assert d.remaining is None

    def test_estimated_accuracy(self) -> None:
        d = Decision(
            allowed=True,
            limit=100,
            remaining=42,
            retry_after_ms=None,
            reset_after_ms=500,
            algorithm=Algorithm.SLIDING_WINDOW_COUNTER,
            reason=DecisionReason.ALLOWED,
            accuracy=Accuracy.ESTIMATED,
        )
        assert d.accuracy == Accuracy.ESTIMATED


# ── Subject ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestSubject:
    """Verify Subject data model."""

    def test_valid_subject(self) -> None:
        s = Subject(kind=IdentityKind.IP, identity="192.168.1.1")
        assert s.kind == IdentityKind.IP
        assert s.identity == "192.168.1.1"

    def test_empty_identity_rejected(self) -> None:
        with pytest.raises(ValueError, match="identity must be non-empty"):
            Subject(kind=IdentityKind.USER, identity="")

    def test_subject_is_frozen(self) -> None:
        s = Subject(kind=IdentityKind.IP, identity="1.2.3.4")
        with pytest.raises(AttributeError):
            s.identity = "5.6.7.8"  # type: ignore[misc]
