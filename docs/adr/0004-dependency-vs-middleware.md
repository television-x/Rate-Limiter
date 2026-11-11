# ADR 0004: FastAPI Dependencies vs Middleware for Rate Limiting

## Status

Accepted (Phase 6)

## Context

In an HTTP API built with FastAPI / Starlette, cross-cutting concerns can be handled either in ASGI middleware (wrapping every request) or in FastAPI dependencies (`Depends(...)` at route or router level).

Rate limiting has distinct characteristics:
1. Different routes require different policies (e.g. `/ping` uses `default` with 60 req/min, `/strict` uses 5 req/10s, dynamic algorithm routes use custom algorithms).
2. Rate limiting requires route-level parameterization (policy ID, request cost).
3. Rate limiting requires access to route-specific dependencies, request body, and parsed path parameters.
4. Setting rate limit headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`) must apply to both allowed (200 OK) responses and rejected (429 Too Many Requests) error responses.
5. Other concerns (request ID propagation, `X-Served-By`, overall request latency metrics, `Server-Timing`) apply universally to every HTTP request regardless of whether rate limiting is active on that route.

## Decision

We separate concerns strictly per AGENTS.md §3.5:

1. **Use FastAPI dependencies for rate limiting:**
   - Implemented in `app.api.dependencies.rate_limit(policy_id)`.
   - Injected at route or router level via `Depends(...)`.
   - Evaluates client identity, resolves policy, executes the algorithm limiter, and sets headers on the response.
   - Raises `RateLimitExceeded` on denial, which triggers the RFC 7807 problem details error handler (`rate_limit_exceeded_handler`), returning 429 with all required headers.

2. **Use Starlette middleware exclusively for cross-cutting context:**
   - Implemented in `app.middleware.observability.ObservabilityMiddleware`.
   - Generates or propagates `X-Request-ID`.
   - Attaches `X-Served-By` indicating the serving node/instance.
   - Emits `Server-Timing` headers aggregating execution duration (`limiter;dur=...`, `app;dur=...`).

## Consequences

### Positive
- **Fine-grained policy control:** Routes declare their own policies cleanly and declaratively in code and config.
- **OpenAPI documentation:** FastAPI dependencies participate in OpenAPI schema generation.
- **Layering integrity:** Middleware does not need route-to-policy URL pattern matching logic, avoiding fragile regex table lookups.
- **Clean separation:** Middleware focuses purely on transport/observability metadata; dependencies focus on domain security and limiting.

### Negative / Trade-offs
- Requires route authors to explicitly annotate endpoints with `Depends(rate_limit(...))` rather than having a blanket global middleware intercept everything. (This is intentional, as unmetered endpoints such as `/healthz` and `/metrics` must never be rate-limited).

