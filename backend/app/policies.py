"""Policy registry, YAML loader, and dynamic runtime overrides.

Per AGENTS.md §9:
- Loads and validates rate limiting policies from policies.yaml at startup.
- Runtime policy overrides stored in Redis with a version counter and a 2 s local cache.
- Limited strictly to demo policies within bounded ranges.
- Safe cross-instance propagation.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml

from app.core.models import Algorithm, ContentionMode, FailureMode, IdentityKind, Policy
from app.core.storage.base import Storage

logger = logging.getLogger("app.policies")

# Demo policy IDs that are allowed to be modified at runtime
DEMO_POLICY_IDS = frozenset(
    {
        "default",
        "strict",
        "fixed_window",
        "token_bucket",
        "sliding_window_log",
        "sliding_window_counter",
    }
)

# Bounded range constants
MIN_LIMIT = 1
MAX_LIMIT = 10000
MIN_WINDOW_MS = 1000
MAX_WINDOW_MS = 3600000
MIN_CAPACITY = 1
MAX_CAPACITY = 10000
MIN_REFILL_PER_SEC = 0.1
MAX_REFILL_PER_SEC = 1000.0
MIN_COST_DEFAULT = 1
MAX_COST_DEFAULT = 1000


class PolicyForbiddenError(Exception):
    """Raised when an attempt is made to mutate a non-demo policy."""


def validate_policy_override_bounds(policy: Policy) -> None:
    """Validate that policy override parameters are strictly within bounded ranges."""
    if policy.algorithm == Algorithm.TOKEN_BUCKET:
        if policy.capacity is None or not (MIN_CAPACITY <= policy.capacity <= MAX_CAPACITY):
            raise ValueError(f"capacity must be between {MIN_CAPACITY} and {MAX_CAPACITY}")
        if policy.refill_per_sec is None or not (
            MIN_REFILL_PER_SEC <= policy.refill_per_sec <= MAX_REFILL_PER_SEC
        ):
            raise ValueError(
                f"refill_per_sec must be between {MIN_REFILL_PER_SEC} and {MAX_REFILL_PER_SEC}"
            )
    else:
        if policy.limit is None or not (MIN_LIMIT <= policy.limit <= MAX_LIMIT):
            raise ValueError(f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}")
        if policy.window_ms is None or not (MIN_WINDOW_MS <= policy.window_ms <= MAX_WINDOW_MS):
            raise ValueError(f"window_ms must be between {MIN_WINDOW_MS} and {MAX_WINDOW_MS}")

    if not (MIN_COST_DEFAULT <= policy.cost_default <= MAX_COST_DEFAULT):
        raise ValueError(f"cost_default must be between {MIN_COST_DEFAULT} and {MAX_COST_DEFAULT}")


def policy_to_dict(p: Policy) -> dict[str, Any]:
    """Serialize a Policy to a dictionary."""
    return {
        "id": p.id,
        "algorithm": p.algorithm.value,
        "limit": p.limit,
        "window_ms": p.window_ms,
        "capacity": p.capacity,
        "refill_per_sec": p.refill_per_sec,
        "cost_default": p.cost_default,
        "on_failure": p.on_failure.value,
        "on_contention": p.on_contention.value,
        "scope": [s.value for s in p.scope],
    }


def parse_policy_dict(raw: dict[str, Any]) -> Policy:
    """Parse and validate a dictionary into a Policy model."""
    algo = Algorithm(raw["algorithm"])
    on_failure = FailureMode(raw.get("on_failure", "open"))
    on_contention = ContentionMode(raw.get("on_contention", "reject"))

    scope_raw = raw.get("scope", ["ip"])
    scope = frozenset(IdentityKind(k) for k in scope_raw)

    return Policy(
        id=raw["id"],
        algorithm=algo,
        limit=raw.get("limit"),
        window_ms=raw.get("window_ms"),
        capacity=raw.get("capacity"),
        refill_per_sec=float(raw["refill_per_sec"])
        if raw.get("refill_per_sec") is not None
        else None,
        cost_default=raw.get("cost_default", 1),
        on_failure=on_failure,
        on_contention=on_contention,
        scope=scope,
    )


class PolicyRegistry:
    """Registry holding validated Policy objects with dynamic override support."""

    def __init__(
        self, policies: list[Policy] | None = None, cache_ttl_seconds: float = 2.0
    ) -> None:
        self._base_policies: dict[str, Policy] = {}
        self._overrides: dict[str, Policy] = {}
        self._effective_policies: dict[str, Policy] = {}
        self._cache_ttl = cache_ttl_seconds
        self._last_sync_time: float = 0.0
        self._local_version: int = 0

        if policies:
            for p in policies:
                self.register(p)

    def register(self, policy: Policy) -> None:
        """Register a base policy in the registry."""
        self._base_policies[policy.id] = policy
        self._recompute_effective(policy.id)

    def _recompute_effective(self, policy_id: str | None = None) -> None:
        """Update effective policies by merging base with overrides."""
        target_ids = [policy_id] if policy_id is not None else list(self._base_policies.keys())
        for pid in target_ids:
            if pid in self._overrides:
                self._effective_policies[pid] = self._overrides[pid]
            elif pid in self._base_policies:
                self._effective_policies[pid] = self._base_policies[pid]

    def get(self, policy_id: str) -> Policy:
        """Get the current cached effective policy by ID, raising KeyError if not found."""
        if policy_id in self._effective_policies:
            return self._effective_policies[policy_id]
        if policy_id in self._base_policies:
            return self._base_policies[policy_id]
        raise KeyError(f"Policy '{policy_id}' not found in registry")

    async def get_effective(self, policy_id: str, storage: Storage | None = None) -> Policy:
        """Get effective policy, syncing from storage if 2-second cache expired."""
        now = time.monotonic()
        if storage is not None and (now - self._last_sync_time >= self._cache_ttl):
            await self.sync_overrides(storage)
        return self.get(policy_id)

    def list_all(self) -> list[Policy]:
        """Return all current effective policies."""
        return list(self._effective_policies.values())

    def __contains__(self, policy_id: str) -> bool:
        return policy_id in self._effective_policies or policy_id in self._base_policies

    async def set_override(self, policy: Policy, storage: Storage | None = None) -> Policy:
        """Set a runtime override for a demo policy and persist to storage."""
        if policy.id not in DEMO_POLICY_IDS:
            raise PolicyForbiddenError(
                f"Policy '{policy.id}' is not a demo policy; only demo policies may be modified at runtime."
            )
        if policy.id not in self._base_policies:
            raise KeyError(f"Policy '{policy.id}' does not exist in base registry")

        validate_policy_override_bounds(policy)

        self._overrides[policy.id] = policy
        self._recompute_effective(policy.id)
        self._local_version += 1
        self._last_sync_time = time.monotonic()

        # Persist to storage if available
        if storage is not None:
            await self._persist_override_to_storage(policy, storage)

        return policy

    async def _persist_override_to_storage(self, policy: Policy, storage: Storage) -> None:
        """Persist policy override and increment version in storage via optimistic transaction."""
        data_str = json.dumps(policy_to_dict(policy))
        try:
            async with await storage.optimistic(["rl:v1:policy_overrides_version"]) as txn:
                ver_raw = await txn.get("rl:v1:policy_overrides_version")
                cur_ver = int(ver_raw) if ver_raw is not None else 0
                new_ver = cur_ver + 1
                txn.set("rl:v1:policy_overrides_version", str(new_ver))
                txn.hset("rl:v1:policy_overrides", {policy.id: data_str})
                ok = await txn.commit()
                if ok:
                    self._local_version = new_ver
        except Exception as exc:
            logger.warning("Failed to persist policy override to storage: %s", exc)

    async def sync_overrides(self, storage: Storage) -> None:
        """Synchronize overrides from storage if remote version is newer."""
        self._last_sync_time = time.monotonic()
        try:
            async with await storage.optimistic(["rl:v1:policy_overrides_version"]) as txn:
                ver_raw = await txn.get("rl:v1:policy_overrides_version")
                remote_ver = int(ver_raw) if ver_raw is not None else 0
                if remote_ver > self._local_version:
                    raw_map = await txn.hgetall("rl:v1:policy_overrides")
                    for _, data_str in raw_map.items():
                        parsed = parse_policy_dict(json.loads(data_str))
                        self._overrides[parsed.id] = parsed
                    self._recompute_effective()
                    self._local_version = remote_ver
        except Exception as exc:
            logger.debug("Failed to sync policy overrides from storage: %s", exc)


def load_policies_from_yaml(path: str | Path) -> PolicyRegistry:
    """Load and validate policies from a YAML configuration file."""
    file_path = Path(path)
    if not file_path.is_file():
        alt_path = Path("backend") / path
        if alt_path.is_file():
            file_path = alt_path
        else:
            raise FileNotFoundError(f"Policies YAML file not found at: {path}")

    with open(file_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "policies" not in data:
        raise ValueError("Invalid policies YAML: root must be a dict containing 'policies' list")

    registry = PolicyRegistry()
    for raw in data["policies"]:
        policy = parse_policy_dict(raw)
        registry.register(policy)

    return registry
