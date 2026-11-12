"""Statistics service for rate limiting operations.

Per AGENTS.md §9:
- Bounded per-second counters per policy (TTL ~5 min / 300 seconds).
- Capped top-N clients using truncated hashed IDs (SHA-256 truncated to 12 hex chars).
- Dense rolling time series generation for frontend charts.
- Bounded memory consumption with automated pruning.

Must not import FastAPI or Prometheus.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from dataclasses import dataclass, field

# Retention window in seconds (5 minutes)
RETENTION_SECONDS = 300

# Truncated hash length for client identities
CLIENT_HASH_LENGTH = 12

# Maximum distinct client hashes retained per second bucket to prevent memory attacks
MAX_CLIENTS_PER_BUCKET = 500


def hash_client_id(client_id: str) -> str:
    """Hash a raw client identifier and truncate to 12 hex characters.

    Ensures no PII (e.g. raw IP, user ID, API key) is stored or exposed
    in admin analytics.
    """
    digest = hashlib.sha256(client_id.encode("utf-8")).hexdigest()
    return digest[:CLIENT_HASH_LENGTH]


@dataclass
class SecondBucket:
    """Counter bucket for a single 1-second timestamp window."""

    timestamp_s: int
    allowed: int = 0
    rejected: int = 0
    # client_hash -> [allowed_count, rejected_count]
    clients: dict[str, list[int]] = field(default_factory=dict)

    def record(self, allowed: bool, client_hash: str) -> None:
        """Record a single decision into this second bucket."""
        if allowed:
            self.allowed += 1
        else:
            self.rejected += 1

        if client_hash in self.clients:
            idx = 0 if allowed else 1
            self.clients[client_hash][idx] += 1
        elif len(self.clients) < MAX_CLIENTS_PER_BUCKET:
            counts = [1, 0] if allowed else [0, 1]
            self.clients[client_hash] = counts


@dataclass(frozen=True)
class StatsPoint:
    """A single point in time series data."""

    timestamp_s: int
    allowed: int
    rejected: int


@dataclass(frozen=True)
class TopClientStats:
    """Usage statistics for a single client identified by its hash."""

    client_hash: str
    allowed: int
    rejected: int
    total: int


@dataclass(frozen=True)
class PolicyStatsSummary:
    """Aggregated stats response for a policy over a given time window."""

    policy_id: str
    window_s: int
    total_allowed: int
    total_rejected: int
    series: list[StatsPoint]
    top_clients: list[TopClientStats]


class StatsService:
    """In-memory rolling statistics service with strict retention and memory bounds."""

    def __init__(self, retention_seconds: int = RETENTION_SECONDS) -> None:
        self._retention_seconds = retention_seconds
        # policy_id -> timestamp_s -> SecondBucket
        self._buckets: dict[str, dict[int, SecondBucket]] = defaultdict(dict)

    def record_decision(
        self,
        policy_id: str,
        allowed: bool,
        client_id: str,
        timestamp_s: int | None = None,
    ) -> None:
        """Record an allowed or rejected rate limit decision.

        Args:
            policy_id: The policy identifier.
            allowed: True if request was allowed, False if rejected (429).
            client_id: Raw client identifier to hash.
            timestamp_s: Optional epoch second timestamp (defaults to current time).
        """
        now_s = int(time.time()) if timestamp_s is None else timestamp_s
        client_hash = hash_client_id(client_id)

        policy_buckets = self._buckets[policy_id]
        if now_s not in policy_buckets:
            policy_buckets[now_s] = SecondBucket(timestamp_s=now_s)

        policy_buckets[now_s].record(allowed=allowed, client_hash=client_hash)

        # Opportunistic prune of old data for this policy
        self._prune_policy(policy_id, now_s)

    def prune(self, now_s: int | None = None) -> None:
        """Prune all buckets older than the retention window across all policies."""
        current_time = int(time.time()) if now_s is None else now_s
        for policy_id in list(self._buckets.keys()):
            self._prune_policy(policy_id, current_time)

    def _prune_policy(self, policy_id: str, current_time: int) -> None:
        """Drop buckets older than current_time - retention_seconds."""
        cutoff = current_time - self._retention_seconds
        policy_buckets = self._buckets[policy_id]
        old_timestamps = [ts for ts in policy_buckets if ts < cutoff]
        for ts in old_timestamps:
            del policy_buckets[ts]

    def get_stats(
        self,
        policy_id: str,
        window_s: int = 60,
        top_n: int = 10,
        now_s: int | None = None,
    ) -> PolicyStatsSummary:
        """Retrieve aggregated statistics and time series for a policy.

        Args:
            policy_id: Target policy identifier.
            window_s: Length of time window in seconds (1 <= window_s <= retention_seconds).
            top_n: Number of top active clients to return.
            now_s: Current epoch second (defaults to system time).

        Returns:
            A PolicyStatsSummary containing dense second-by-second series
            and top-N client breakdowns.
        """
        current_time = int(time.time()) if now_s is None else now_s
        bounded_window = max(1, min(window_s, self._retention_seconds))
        start_time = current_time - bounded_window + 1

        policy_buckets = self._buckets.get(policy_id, {})

        series: list[StatsPoint] = []
        total_allowed = 0
        total_rejected = 0
        client_agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])

        for sec in range(start_time, current_time + 1):
            bucket = policy_buckets.get(sec)
            if bucket is not None:
                series.append(
                    StatsPoint(
                        timestamp_s=sec,
                        allowed=bucket.allowed,
                        rejected=bucket.rejected,
                    )
                )
                total_allowed += bucket.allowed
                total_rejected += bucket.rejected

                for chash, (a, r) in bucket.clients.items():
                    client_agg[chash][0] += a
                    client_agg[chash][1] += r
            else:
                series.append(StatsPoint(timestamp_s=sec, allowed=0, rejected=0))

        # Sort clients by total requests descending
        sorted_clients = sorted(
            client_agg.items(),
            key=lambda item: item[1][0] + item[1][1],
            reverse=True,
        )

        top_clients: list[TopClientStats] = [
            TopClientStats(
                client_hash=chash,
                allowed=counts[0],
                rejected=counts[1],
                total=counts[0] + counts[1],
            )
            for chash, counts in sorted_clients[:top_n]
        ]

        return PolicyStatsSummary(
            policy_id=policy_id,
            window_s=bounded_window,
            total_allowed=total_allowed,
            total_rejected=total_rejected,
            series=series,
            top_clients=top_clients,
        )

    def clear(self) -> None:
        """Clear all stored statistics."""
        self._buckets.clear()
