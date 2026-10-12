"""Unit tests for the key builder.

Tests collision safety, kind tags, hashing, versioning, and hash-tag
grouping per AGENTS.md §3.3.
"""

from __future__ import annotations

import pytest

from app.core.key_builder import _hash_identity, build_key
from app.core.models import Algorithm, IdentityKind


@pytest.mark.unit
class TestHashIdentity:
    """Verify identity hashing properties."""

    def test_deterministic(self) -> None:
        h1 = _hash_identity("test@example.com")
        h2 = _hash_identity("test@example.com")
        assert h1 == h2

    def test_fixed_length(self) -> None:
        h = _hash_identity("any-string-here")
        assert len(h) == 16  # truncated SHA-256

    def test_hex_characters(self) -> None:
        h = _hash_identity("hello")
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_inputs_produce_different_hashes(self) -> None:
        h1 = _hash_identity("user1")
        h2 = _hash_identity("user2")
        assert h1 != h2


@pytest.mark.unit
class TestBuildKey:
    """Verify key construction rules."""

    def test_basic_format(self) -> None:
        key = build_key("default", IdentityKind.IP, "192.168.1.1", Algorithm.FIXED_WINDOW)
        assert key.startswith("rl:v1:{")
        assert "|ip:" in key
        assert "}:fixed_window" in key

    def test_with_suffix(self) -> None:
        key = build_key(
            "default",
            IdentityKind.IP,
            "192.168.1.1",
            Algorithm.FIXED_WINDOW,
            suffix="12345",
        )
        assert key.endswith(":12345")

    def test_without_suffix(self) -> None:
        key = build_key("default", IdentityKind.IP, "192.168.1.1", Algorithm.FIXED_WINDOW)
        # Should end at the algorithm, no trailing colon
        assert key.endswith(":fixed_window")

    def test_version_prefix(self) -> None:
        key = build_key("p1", IdentityKind.USER, "alice", Algorithm.TOKEN_BUCKET)
        assert key.startswith("rl:v1:")

    def test_hash_tag_grouping(self) -> None:
        """Keys for the same policy+identity share a hash-tag group."""
        key1 = build_key("p1", IdentityKind.IP, "10.0.0.1", Algorithm.FIXED_WINDOW, "curr")
        key2 = build_key("p1", IdentityKind.IP, "10.0.0.1", Algorithm.FIXED_WINDOW, "prev")
        # Extract hash tag content (between { and })
        tag1 = key1.split("{")[1].split("}")[0]
        tag2 = key2.split("{")[1].split("}")[0]
        assert tag1 == tag2

    def test_kind_prevents_collision(self) -> None:
        """user:1 and ip:1 must produce different keys."""
        key_user = build_key("p1", IdentityKind.USER, "1", Algorithm.FIXED_WINDOW)
        key_ip = build_key("p1", IdentityKind.IP, "1", Algorithm.FIXED_WINDOW)
        assert key_user != key_ip

    def test_different_policies_different_keys(self) -> None:
        key1 = build_key("policy_a", IdentityKind.IP, "1.2.3.4", Algorithm.FIXED_WINDOW)
        key2 = build_key("policy_b", IdentityKind.IP, "1.2.3.4", Algorithm.FIXED_WINDOW)
        assert key1 != key2

    def test_different_algorithms_different_keys(self) -> None:
        key1 = build_key("p1", IdentityKind.IP, "1.2.3.4", Algorithm.FIXED_WINDOW)
        key2 = build_key("p1", IdentityKind.IP, "1.2.3.4", Algorithm.TOKEN_BUCKET)
        assert key1 != key2

    def test_delimiter_injection_prevented(self) -> None:
        """Identity with delimiters is safely hashed away."""
        key_normal = build_key("p1", IdentityKind.USER, "alice", Algorithm.FIXED_WINDOW)
        key_injected = build_key(
            "p1", IdentityKind.USER, "alice}:fixed_window:fake", Algorithm.FIXED_WINDOW
        )
        # The injected identity gets hashed, so it can't break the key format
        assert key_normal != key_injected
        # Both should have exactly one { and one }
        assert key_injected.count("{") == 1
        assert key_injected.count("}") == 1

    def test_all_identity_kinds(self) -> None:
        """All IdentityKind values produce valid keys."""
        for kind in IdentityKind:
            key = build_key("test", kind, "some-id", Algorithm.FIXED_WINDOW)
            assert f"|{kind.value}:" in key

    def test_all_algorithms(self) -> None:
        """All Algorithm values produce valid keys."""
        for algo in Algorithm:
            key = build_key("test", IdentityKind.IP, "1.1.1.1", algo)
            assert f":{algo.value}" in key

    def test_long_identity_bounded(self) -> None:
        """Even very long identities produce fixed-length keys."""
        short_key = build_key("p", IdentityKind.IP, "x", Algorithm.FIXED_WINDOW)
        long_key = build_key("p", IdentityKind.IP, "x" * 10000, Algorithm.FIXED_WINDOW)
        # Keys should be similar length (identity is hashed to fixed length)
        assert abs(len(short_key) - len(long_key)) == 0
