"""Unit tests for the StatsService.

Verify retention bounds (TTL 300s), dense series aggregation,
client hash truncation, and top-N client calculations.
"""

from __future__ import annotations

import pytest

from app.services.stats import (
    CLIENT_HASH_LENGTH,
    MAX_CLIENTS_PER_BUCKET,
    StatsService,
    hash_client_id,
)


@pytest.mark.unit
class TestStatsService:
    """Tests for StatsService behavior."""

    def test_client_id_is_hashed_and_truncated(self) -> None:
        raw_ip = "192.168.1.100"
        h = hash_client_id(raw_ip)
        assert len(h) == CLIENT_HASH_LENGTH
        assert raw_ip not in h
        # Deterministic
        assert h == hash_client_id(raw_ip)

    def test_record_and_get_stats_aggregates_correctly(self) -> None:
        service = StatsService()
        now = 1000

        # Record requests at timestamp now
        service.record_decision("default", allowed=True, client_id="user1", timestamp_s=now)
        service.record_decision("default", allowed=True, client_id="user1", timestamp_s=now)
        service.record_decision("default", allowed=False, client_id="user2", timestamp_s=now)

        summary = service.get_stats("default", window_s=10, now_s=now)
        assert summary.policy_id == "default"
        assert summary.window_s == 10
        assert summary.total_allowed == 2
        assert summary.total_rejected == 1
        assert len(summary.series) == 10
        # The last point in series should match timestamp now
        assert summary.series[-1].timestamp_s == now
        assert summary.series[-1].allowed == 2
        assert summary.series[-1].rejected == 1

        # Check top clients
        assert len(summary.top_clients) == 2
        h1 = hash_client_id("user1")
        h2 = hash_client_id("user2")

        c1 = next(c for c in summary.top_clients if c.client_hash == h1)
        assert c1.allowed == 2
        assert c1.rejected == 0
        assert c1.total == 2

        c2 = next(c for c in summary.top_clients if c.client_hash == h2)
        assert c2.allowed == 0
        assert c2.rejected == 1
        assert c2.total == 1

    def test_stats_pruning_drops_data_older_than_retention(self) -> None:
        service = StatsService(retention_seconds=300)

        # Record at t=100
        service.record_decision("default", allowed=True, client_id="user1", timestamp_s=100)

        # At t=400 (diff=300, cutoff=100), bucket 100 is still retained
        service.prune(now_s=400)
        assert 100 in service._buckets["default"]

        # At t=401 (diff=301, cutoff=101), bucket 100 is pruned
        service.prune(now_s=401)
        assert 100 not in service._buckets["default"]

    def test_automatic_pruning_on_record(self) -> None:
        service = StatsService(retention_seconds=300)
        service.record_decision("default", allowed=True, client_id="user1", timestamp_s=100)

        # Recording at t=450 triggers prune of t=100
        service.record_decision("default", allowed=True, client_id="user2", timestamp_s=450)
        assert 100 not in service._buckets["default"]
        assert 450 in service._buckets["default"]

    def test_dense_time_series_fills_zeroes_for_missing_seconds(self) -> None:
        service = StatsService()
        now = 1000

        # Only record at t=995 and t=1000
        service.record_decision("default", allowed=True, client_id="u", timestamp_s=995)
        service.record_decision("default", allowed=False, client_id="u", timestamp_s=1000)

        summary = service.get_stats("default", window_s=6, now_s=now)
        # Window of 6 seconds: 995, 996, 997, 998, 999, 1000
        assert len(summary.series) == 6
        assert summary.series[0].timestamp_s == 995
        assert summary.series[0].allowed == 1
        assert summary.series[1].timestamp_s == 996
        assert summary.series[1].allowed == 0
        assert summary.series[1].rejected == 0
        assert summary.series[-1].timestamp_s == 1000
        assert summary.series[-1].rejected == 1

    def test_client_count_cap_per_bucket_prevents_unbounded_growth(self) -> None:
        service = StatsService()
        now = 1000

        # Insert MAX_CLIENTS_PER_BUCKET distinct clients
        for i in range(MAX_CLIENTS_PER_BUCKET):
            service.record_decision(
                "default", allowed=True, client_id=f"client_{i}", timestamp_s=now
            )

        b = service._buckets["default"][now]
        assert len(b.clients) == MAX_CLIENTS_PER_BUCKET

        # Inserting one more does not expand dict beyond limit
        service.record_decision(
            "default", allowed=True, client_id="overflow_client", timestamp_s=now
        )
        assert len(b.clients) == MAX_CLIENTS_PER_BUCKET
        # But aggregate allowed still increments
        assert b.allowed == MAX_CLIENTS_PER_BUCKET + 1
