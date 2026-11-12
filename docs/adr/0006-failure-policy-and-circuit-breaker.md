# ADR 0006: Failure Handling Strategy and Circuit Breaker Pattern

## Status

Accepted (Phase 7)

## Context

Distributed rate limiters rely on a shared datastore (Redis) to synchronize quota decisions across multiple API instances. When the shared datastore becomes slow, experiences transient network partitions, or crashes entirely, the rate limiter must have well-defined, predictable, and resilient degradation behaviors.

Key requirements:
1. Prevent failing storage from overwhelming the backend or causing request queuing and cascading thread/connection exhaustion.
2. Provide configurable per-route / per-policy trade-offs between availability and quota enforcement (fail-open vs fail-closed vs local in-memory fallback).
3. Clearly distinguish and label degraded behavior on outgoing HTTP responses so downstream consumers and operational monitoring know enforcement is impaired (`degraded=true`, `X-RateLimit-Status: degraded`).
4. Avoid confusing local in-memory fallback with distributed enforcement (an in-memory fallback on $N$ instances allows up to $N \times L$ requests).
5. Recover automatically when storage returns to healthy operation without requiring an application restart.

## Decision

We implement a two-layered resilience architecture:

### 1. Storage Protection: Circuit Breaker Pattern (`app.core.circuit_breaker`)
- Implemented as a state machine (`CircuitBreaker`) and storage decorator (`CircuitBreakerStorage`).
- **States:**
  - `CLOSED`: Normal operation. All operations execute against Redis. Consecutive failures are tracked.
  - `OPEN`: Tripped after $N$ (default 3) consecutive storage failures. All operations fast-fail immediately with `StorageUnavailable`, completely bypassing Redis and preventing connection pileups.
  - `HALF_OPEN`: Entered after a cooldown period (default 5000 ms). Permits a single probe request to test Redis health. If the probe succeeds, the breaker transitions back to `CLOSED`. If the probe fails, it trips back to `OPEN`.
- Emits observer events (`on_breaker_state`) on every transition for Prometheus observability.

### 2. Policy-Level Degradation Resolution (`app.core.failure_policy`)
Wrapped via `ResilientLimiter` and governed by `Policy.on_failure` and `Policy.on_contention`:
- **`on_failure: open`**: Prioritizes service availability. The request is allowed, remaining quota is set to `None`, `degraded=True`, and the HTTP response carries `X-RateLimit-Status: degraded`.
- **`on_failure: closed`**: Prioritizes quota security and downstream system protection. Re-raises `StorageUnavailable`, resulting in an HTTP 503 Service Unavailable response with `Retry-After: 1` and RFC 7807 problem details.
- **`on_failure: local_fallback`**: Evaluates the request against an isolated per-instance `MemoryStorage` limiter. All decisions are explicitly flagged with `degraded=True` and `X-RateLimit-Status: degraded` to clearly signify that global distributed enforcement is inactive.
- **`on_contention`**: Governs behavior when optimistic transaction retries exhaust under extreme concurrency. `reject` raises `ContentionExhausted` (HTTP 429); `allow` degrades open.

## Consequences

### Positive
- **Fault isolation:** Downstream database or Redis outages do not cascade into HTTP worker timeouts.
- **Transparent recovery:** Applications resume full distributed enforcement immediately after Redis recovers without restarts.
- **Explicit degradation labeling:** Per Rule 5, degraded decisions are permanently and prominently labeled on headers and domain models.

### Negative / Trade-offs
- In `local_fallback` mode, an aggregate of up to $N \times L$ requests may be allowed across $N$ API nodes during a Redis outage. This trade-off is explicitly documented and tracked via the degraded status indicator.

