"""Limiter interface and factory for rate-limiting algorithms.

Defines the abstract base class that all algorithms implement and
the factory that maps Policy -> Limiter. The API layer never names
a concrete algorithm directly.

Must not import FastAPI, Redis, or Prometheus.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.models import Algorithm, Decision, Policy
from app.core.observer import NullObserver, Observer
from app.core.storage.base import Storage


class Limiter(ABC):
    """Abstract rate limiter.

    All algorithms implement this interface. Configuration is injected
    at construction time via a Policy.

    Usage:
        decision = await limiter.check(key, cost=1)
    """

    def __init__(
        self,
        policy: Policy,
        storage: Storage,
        observer: Observer | None = None,
    ) -> None:
        self._policy = policy
        self._storage = storage
        self._observer = observer or NullObserver()

    @property
    def policy(self) -> Policy:
        """The policy this limiter enforces."""
        return self._policy

    @abstractmethod
    async def check(self, key: str, cost: int = 1) -> Decision:
        """Check whether a request should be allowed.

        Args:
            key: The fully-constructed storage key (from key_builder).
            cost: The cost of this request (default 1).

        Returns:
            A Decision indicating whether the request is allowed,
            with remaining quota, retry timing, and accuracy info.
        """


class LimiterFactory:
    """Maps Policy -> Limiter using a registry of algorithm implementations.

    Register algorithm implementations at startup. The factory
    ensures the API never names a concrete algorithm.
    """

    def __init__(self) -> None:
        self._registry: dict[Algorithm, type[Limiter]] = {}

    def register(self, algorithm: Algorithm, limiter_cls: type[Limiter]) -> None:
        """Register a Limiter implementation for an algorithm.

        Args:
            algorithm: The algorithm enum value.
            limiter_cls: The concrete Limiter class.
        """
        self._registry[algorithm] = limiter_cls

    def create(
        self,
        policy: Policy,
        storage: Storage,
        observer: Observer | None = None,
    ) -> Limiter:
        """Create a Limiter for the given policy.

        Args:
            policy: The rate-limit policy.
            storage: The storage backend.
            observer: Optional event observer.

        Returns:
            A configured Limiter instance.

        Raises:
            ValueError: If no implementation is registered for the
                        policy's algorithm.
        """
        cls = self._registry.get(policy.algorithm)
        if cls is None:
            raise ValueError(f"No limiter registered for algorithm: {policy.algorithm.value}")
        return cls(policy, storage, observer)

    @property
    def registered_algorithms(self) -> list[Algorithm]:
        """List all registered algorithms."""
        return list(self._registry.keys())
