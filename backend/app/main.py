"""FastAPI application factory and lifecycle management.

Per AGENTS.md §6 Phase 6:
- Lifespan creates and closes the Redis pool / storage backend
- Registers RFC 7807 problem details exception handlers
- Attaches observability middleware
- Mounts health, demo, and policy routes
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import (
    RateLimitExceeded,
    contention_exhausted_handler,
    invalid_identity_handler,
    rate_limit_exceeded_handler,
    storage_unavailable_handler,
)
from app.api.routes_demo import router as demo_router
from app.api.routes_health import router as health_router
from app.api.routes_policies import router as policies_router
from app.config import Settings, get_settings
from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerStorage
from app.core.clock import Clock, SystemClock
from app.core.errors import ContentionExhausted, InvalidIdentity, StorageUnavailable
from app.core.limiter import LimiterFactory, get_default_limiter_factory
from app.core.observer import NullObserver, Observer
from app.core.storage.base import Storage
from app.core.storage.memory import MemoryStorage
from app.middleware.observability import ObservabilityMiddleware
from app.policies import PolicyRegistry, load_policies_from_yaml

logger = logging.getLogger("app.main")


def create_app(
    settings: Settings | None = None,
    storage: Storage | None = None,
    fallback_storage: Storage | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    policy_registry: PolicyRegistry | None = None,
    limiter_factory: LimiterFactory | None = None,
    observer: Observer | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Create and configure a FastAPI rate limiter application instance."""
    app_settings = settings or get_settings()
    app_clock = clock or SystemClock()
    app_observer = observer or NullObserver()
    app_factory = limiter_factory or get_default_limiter_factory()
    app_breaker = circuit_breaker or CircuitBreaker(clock=app_clock, observer=app_observer)
    app_fallback = fallback_storage or MemoryStorage(clock=app_clock)

    # 1. Policies
    if policy_registry is not None:
        registry = policy_registry
    else:
        try:
            registry = load_policies_from_yaml(app_settings.policies_path)
        except Exception as exc:
            logger.warning(
                "Failed to load policies from '%s': %s. Using empty registry.",
                app_settings.policies_path,
                exc,
            )
            registry = PolicyRegistry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Connect storage if not already injected
        if not hasattr(app.state, "storage") or app.state.storage is None:
            try:
                from app.core.storage.redis_store import RedisStorage

                r_storage = RedisStorage(url=app_settings.redis_url)
                # Verify Redis connectivity
                await r_storage.client.ping()
                app.state.storage = CircuitBreakerStorage(r_storage, app_breaker)
                logger.info(
                    "Connected to Redis at %s (wrapped with CircuitBreaker)", app_settings.redis_url
                )
            except Exception as exc:
                logger.warning(
                    "Redis unavailable at %s (%s). Falling back to MemoryStorage.",
                    app_settings.redis_url,
                    exc,
                )
                app.state.storage = MemoryStorage(clock=app_clock)

        yield

        # Shutdown: close storage
        if hasattr(app.state, "storage") and app.state.storage is not None:
            await app.state.storage.close()

    app = FastAPI(
        title="Distributed Rate Limiter",
        description="Distributed API rate limiter demonstrating 4 algorithms with shared Redis state",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Pre-populate state so it is available even before lifespan starts (e.g. in test clients)
    app.state.settings = app_settings
    app.state.policy_registry = registry
    app.state.limiter_factory = app_factory
    app.state.observer = app_observer
    app.state.circuit_breaker = app_breaker
    app.state.fallback_storage = app_fallback
    if storage is not None:
        app.state.storage = storage

    # Observability middleware
    app.add_middleware(ObservabilityMiddleware)

    # Exception handlers (RFC 7807 application/problem+json)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_exception_handler(InvalidIdentity, invalid_identity_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StorageUnavailable, storage_unavailable_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ContentionExhausted, contention_exhausted_handler)  # type: ignore[arg-type]

    # Routers
    app.include_router(health_router)
    app.include_router(demo_router)
    app.include_router(policies_router)

    return app


app = create_app()
