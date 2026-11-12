# Design Document: Distributed Rate Limiter

## Repository Inventory

Recorded at Phase 0 start. Updated as phases complete.

| Path | Status | Phase | Notes |
|---|---|---|---|
| `AGENTS.md` | **exists** | — | Implementation plan and spec |
| `backend/pyproject.toml` | **exists** | P0 | Dependencies, tooling config |
| `Makefile` | **exists** | P0 | Build/test/deploy targets |
| `.github/workflows/ci.yml` | **exists** | P0 | CI pipeline |
| `docs/adr/0001-no-lua.md` | **exists** | P0 | No-Lua decision record |
| `docs/design.md` | **exists** | P0 | This file |
| `.gitignore` | **exists** | P0 | |
| `.env.example` | **exists** | P0 | |
| `.pre-commit-config.yaml` | **exists** | P0 | |
| `backend/tests/test_smoke.py` | **exists** | P0 | Trivial smoke test |
| `backend/app/__init__.py` | **exists** | P1 | App package |
| `backend/app/core/models.py` | **exists** | P1 | Policy, Decision, Subject |
| `backend/app/core/errors.py` | **exists** | P1 | Typed error classes |
| `backend/app/core/clock.py` | **exists** | P1 | Clock protocol and implementations |
| `backend/app/core/observer.py` | **exists** | P1 | Observer protocol |
| `backend/app/core/key_builder.py` | **exists** | P1 | Collision-safe key construction |
| `backend/app/core/storage/base.py` | **exists** | P1 | Storage contract with atomicity docs |
| `backend/app/core/storage/memory.py` | **exists** | P1 | In-memory storage |
| `backend/app/core/limiter.py` | **exists** | P2 | Limiter ABC and factory |
| `backend/app/core/algorithms/fixed_window.py` | **exists** | P2 | Fixed window algorithm |
| `backend/app/core/algorithms/token_bucket.py` | **exists** | P3 | Token bucket algorithm |
| `backend/app/core/algorithms/sliding_window_log.py` | **exists** | P3 | Sliding window log |
| `backend/app/core/algorithms/sliding_window_counter.py` | **exists** | P3 | Sliding window counter |
| `backend/app/core/storage/redis_store.py` | **exists** | P4/P5 | Redis storage adapter (atomic & optimistic) |
| `backend/app/core/storage/retry.py` | **exists** | P5 | Bounded retry with jitter |
| `backend/app/core/failure_policy.py` | **exists** | P7 | Failure policy resolution |
| `backend/app/core/circuit_breaker.py` | **exists** | P7 | Circuit breaker |
| `backend/app/main.py` | **exists** | P6 | FastAPI app factory |
| `backend/app/config.py` | **exists** | P6 | Pydantic settings |
| `backend/app/policies.py` | **exists** | P6 | Route-to-policy registry |
| `backend/policies.yaml` | **exists** | P6 | Policy configuration |
| `backend/app/api/` | **exists** | P6 | HTTP API layer |
| `backend/app/middleware/` | **exists** | P6/P8 | Observability middleware |
| `backend/app/metrics/prometheus.py` | **exists** | P8 | Prometheus metrics |
| `backend/app/services/stats.py` | **exists** | P9 | Stats service |
| `backend/app/api/routes_admin.py` | **exists** | P9 | Admin API |
| `backend/Dockerfile` | **exists** | P6 | Backend container |
| `docker-compose.yml` | **exists** | P4 | Compose deployment (Redis service) |
| `infra/nginx/nginx.conf` | **exists** | P10 | Load balancer config |
| `infra/prometheus/prometheus.yml` | **exists** | P8 | Prometheus config |
| `frontend/` | proposed | P11 | React dashboard |
| `benchmarks/` | proposed | P12 | k6 benchmark scripts |
| `docs/algorithms.md` | **exists** | P3 | Algorithm documentation |
| `docs/adr/0002-storage-contract-and-time-source.md` | **exists** | P4 | ADR on storage contract and time source |
| `docs/adr/0003-strict-sliding-log-and-optimistic-locking.md` | **exists** | P5 | ADR on strict sliding log and optimistic locking |
| `docs/adr/0004-dependency-vs-middleware.md` | **exists** | P6 | ADR on dependency vs middleware separation |
| `docs/adr/0005-identity-trust-and-client-resolution.md` | **exists** | P6 | ADR on identity trust and client resolution |
| `docs/adr/0006-failure-policy-and-circuit-breaker.md` | **exists** | P7 | ADR on failure handling and circuit breaker |
| `docs/operations.md` | **exists** | P10 | Operations guide |
| `docs/benchmarks.md` | proposed | P12 | Benchmark results |
| `docs/openapi.json` | **exists** | P9 | API contract snapshot |

## Deferred Items

Items explicitly out of scope per AGENTS.md:

- **Redis HA / Cluster**: Single Redis node is a design choice. Failure policy manages impact.
- **Lua scripts**: Deferred as future optimization (see ADR-0001).
- **Distributed locks**: Not needed; optimistic concurrency via WATCH suffices.
- **Kubernetes**: Docker Compose only.
- **OpenTelemetry / tracing**: Prometheus metrics only.

## Architecture

See AGENTS.md §3 for the full architecture. Key principle: dependency arrows point inward only.

```
api/, middleware/ → services/ → core/ ← metrics/ (implements core.observer)
```

`core/` must never import `fastapi`, `prometheus_client`, or outer layers.
`redis` is imported only in `core/storage/redis_store.py`.
