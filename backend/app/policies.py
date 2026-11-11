"""Policy registry and YAML loader.

Loads and validates rate limiting policies from policies.yaml at startup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.models import Algorithm, ContentionMode, FailureMode, IdentityKind, Policy


class PolicyRegistry:
    """Registry holding validated Policy objects indexed by ID."""

    def __init__(self, policies: list[Policy] | None = None) -> None:
        self._policies: dict[str, Policy] = {}
        if policies:
            for p in policies:
                self.register(p)

    def register(self, policy: Policy) -> None:
        """Register a policy in the registry."""
        self._policies[policy.id] = policy

    def get(self, policy_id: str) -> Policy:
        """Get a policy by ID, raising KeyError if not found."""
        if policy_id not in self._policies:
            raise KeyError(f"Policy '{policy_id}' not found in registry")
        return self._policies[policy_id]

    def list_all(self) -> list[Policy]:
        """Return all registered policies."""
        return list(self._policies.values())

    def __contains__(self, policy_id: str) -> bool:
        return policy_id in self._policies


def load_policies_from_yaml(path: str | Path) -> PolicyRegistry:
    """Load and validate policies from a YAML configuration file.

    Args:
        path: Path to policies.yaml file.

    Returns:
        A populated PolicyRegistry.
    """
    file_path = Path(path)
    if not file_path.is_file():
        # Fallback to backend/policies.yaml if path was relative to root
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
        refill_per_sec=float(raw["refill_per_sec"]) if "refill_per_sec" in raw else None,
        cost_default=raw.get("cost_default", 1),
        on_failure=on_failure,
        on_contention=on_contention,
        scope=scope,
    )
