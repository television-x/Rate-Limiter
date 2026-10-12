"""Collision-safe key construction for rate limiter storage.

Key format: rl:v1:{<policy_id>|<kind>:<identity_hash>}:<algo>[:<suffix>]

Design choices (per AGENTS.md §3.3):
- kind: tag prevents cross-kind collisions (user:1 vs ip:1)
- SHA-256 truncated hash bounds key length and prevents delimiter injection
- {…} hash tag keeps current/previous window keys in the same Redis
  Cluster hash slot (future-safe)
- v1 prefix enables schema migration

Must not import FastAPI, Redis, or any outer layer.
"""

from __future__ import annotations

import hashlib

from app.core.models import Algorithm, IdentityKind

# Truncate SHA-256 to 16 hex chars (64 bits). Collision probability
# is negligible for our key space (< 2^32 unique identities).
_HASH_LENGTH = 16


def _hash_identity(identity: str) -> str:
    """Hash an identity string with truncated SHA-256.

    This bounds key length and prevents delimiter injection attacks.

    Args:
        identity: The raw identity string (IP, user ID, API key).

    Returns:
        A fixed-length hex string safe for use in Redis keys.
    """
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return digest[:_HASH_LENGTH]


def build_key(
    policy_id: str,
    kind: IdentityKind,
    identity: str,
    algorithm: Algorithm,
    suffix: str | None = None,
) -> str:
    """Build a collision-safe storage key.

    Args:
        policy_id: The rate-limit policy identifier.
        kind: The identity kind (user, ip, apikey, route).
        identity: The raw identity value.
        algorithm: The rate-limiting algorithm.
        suffix: Optional suffix for algorithm-specific sub-keys
                (e.g. window IDs, "prev", "tokens").

    Returns:
        A Redis-safe key string with hash-tag grouping.

    Examples:
        >>> build_key("default", IdentityKind.IP, "192.168.1.1",
        ...           Algorithm.FIXED_WINDOW, suffix="12345")
        'rl:v1:{default|ip:a1b2c3...}:fixed_window:12345'
    """
    identity_hash = _hash_identity(identity)
    # Hash tag {…} groups related keys into the same Redis Cluster slot
    base = f"rl:v1:{{{policy_id}|{kind.value}:{identity_hash}}}:{algorithm.value}"
    if suffix is not None:
        return f"{base}:{suffix}"
    return base
