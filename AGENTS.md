# AGENTS.md: Distributed Rate Limiter

Instructions for AI coding agents (and humans) working in this repository. Read this file fully before making changes. Work **one phase at a time**, in order. Do not start a phase until the previous phase's acceptance criteria pass.

---

## 1. Project overview

A distributed API rate limiter demonstrating four algorithms, shared state across multiple API instances, concurrency safety, failure handling, monitoring, benchmarking, and a dashboard.

| Concern | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, `redis-py` (asyncio) |
| Shared state | Redis 7.x (single node, `noeviction`) |
| Frontend | React + TypeScript (Vite) |
| Load balancing | Nginx |
| Monitoring | Prometheus |
| Tests / benchmarks | pytest, pytest-asyncio, httpx, k6 |
| Local deployment | Docker, Docker Compose |

**Algorithms:** Fixed Window, Sliding Window Log, Sliding Window Counter, Token Bucket.

---

## 2. Non-negotiable rules

1. **No Lua scripts** in the initial implementation. Mention Lua only as a future optimization in docs/ADRs.
2. **Inspect before assuming.** Check which files exist before creating or overwriting anything. Reuse existing code, names, and abstractions. Never claim a file or feature exists without evidence.
3. **Never invent results.** No fabricated benchmark numbers, test results, or capabilities. Every number in docs must trace to a committed raw result.
4. **Be precise about atomicity.** A list of commands inside `MULTI/EXEC` does **not** make a *conditional* decision atomic, because you cannot branch on a result inside the transaction. Do not claim strict correctness unless the Redis interaction provides it.
5. **Local in-memory fallback is not distributed enforcement.** Always label degraded behavior (`degraded=true`, header, metric).
6. **Contracts before implementations.** Define and test interfaces before adding interchangeable implementations.
7. **Tests in every phase.** Never defer all testing to the end.
8. **Correctness before visualization.** The frontend starts only after the Phase 9 API contract freeze.
9. **Avoid premature complexity.** No Kubernetes, distributed locks, extra services, or Redis HA unless requirements change.
10. **No empty placeholder files** and no abstraction layers without a clear purpose.
11. **Do not silently drop requirements.** If something is deferred, record it in `docs/design.md`.

---

## 3. Architecture and layering

Dependency direction (arrows point inward only):

```
api/ , middleware/  ->  services/  ->  core/  <-  metrics/ (implements core.observer)
```

| Layer | May import | Must NOT import |
|---|---|---|
| `core/` | stdlib, its own modules; `redis` **only** in `core/storage/redis_store.py` | `fastapi`, `prometheus_client`, `app.api`, `app.services` |
| `core/algorithms/` | `storage/base.py`, `models.py`, `clock.py`, `observer.py`, `errors.py` | `redis`, any HTTP code |
| `api/` | `core`, `services` | `redis` directly |
| `metrics/` | `core.observer` | `api` |
| `tests/` | anything | n/a |

Enforce with an import-boundary check (import-linter or a grep script) in CI.

### 3.1 Domain contracts

**Policy** (validated at load time): `id`, `algorithm`, `limit` (or `capacity`), `window_ms` (or `refill_per_sec`), `cost_default`, `on_failure` (`open | closed | local_fallback`), `on_contention` (`reject | allow`), `scope` (identity kinds composing the key). Rules: positive values, `window_ms >= 1000`, `refill > 0`, `capacity >= cost`.

**Decision:** `allowed`, `limit`, `remaining` (nullable), `retry_after_ms` (nullable), `reset_after_ms` (nullable), `algorithm`, `reason` (`ALLOWED | LIMIT_EXCEEDED | DEGRADED_ALLOWED | STORAGE_UNAVAILABLE | CONTENTION_EXHAUSTED`), `degraded: bool`, `accuracy` (`exact | estimated`).

**Limiter (abstract):** `async check(key: str, cost: int = 1) -> Decision`. Config injected at construction. A `LimiterFactory` maps `Policy -> Limiter`, so the API never names a concrete algorithm.

**Observer (protocol, all no-ops by default):** `on_decision`, `on_storage_op(op, seconds, ok)`, `on_txn_conflict`, `on_txn_retry_exhausted`, `on_breaker_state`.

### 3.2 Storage contract

A generic `get/set/incr` interface is **not** sufficient. The contract is capability-based:

| Operation | Atomicity guarantee | Used by |
|---|---|---|
| `now_ms()` | Single authoritative clock for all instances (Redis `TIME`) | all |
| `incr_expiring(key, by, ttl_ms) -> int` | Increment and TTL set are one atomic unit; TTL set unconditionally (idempotent) | fixed window |
| `incr_expiring_peek(key, by, ttl_ms, peek_key) -> (new, peek_or_0)` | One `MULTI/EXEC`; returns post-increment value | sliding counter |
| `decr(key, by)` | Atomic itself, but **separate** from the earlier incr (compensation) | sliding counter |
| `optimistic(watch_keys) -> Txn` | `Txn` provides reads, queued writes, `commit() -> bool` (False = conflict, nothing written) | token bucket, sliding log |

The memory store is **stronger** than Redis (a single lock makes it linearizable). Memory tests therefore **cannot prove Redis concurrency correctness**. Use the shared contract suite for semantics and a Redis-only concurrency suite for races. The memory store must expose a conflict-injection hook for deterministic retry tests.

### 3.3 Key design

`rl:v1:{<policy_id>|<kind>:<identity_hash>}:<algo>[:<suffix>]`

- `kind:` tag (`user`, `ip`, `apikey`, `route`) prevents `user:1` colliding with `ip:1`.
- Identities hashed (SHA-256, truncated) to bound length and prevent delimiter injection.
- `{...}` hash tag keeps current and previous window keys in one slot (Cluster-safe in future).
- `v1` allows schema migration.

### 3.4 Identity

- Priority: API key header, then `X-User-Id` (only when `DEMO_MODE=true`), then client IP.
- Trust `X-Forwarded-For` / `X-Real-IP` **only** when the direct peer is in `TRUSTED_PROXIES`. Nginx must overwrite (not append) the header.
- Missing identity falls back to IP. Malformed identity (overlong, control chars) returns `400`.

### 3.5 Dependencies vs middleware

Use **FastAPI dependencies** for rate limiting (route-level policy, identity access, header setting). Use **middleware** only for cross-cutting context: request ID, `X-Served-By`, `Server-Timing`, HTTP latency metrics.

---

## 4. Common commands

```bash
make lint                       # ruff + mypy + import-boundary check
make test                       # unit + contract + api (no Docker needed)
pytest -m unit                  # unit only
pytest -m "unit or contract"
pytest -m "integration or concurrency"   # needs Redis (docker compose up -d redis)
pytest -m stress                # manual/nightly only
make up / make down             # docker compose
make bench                      # k6 benchmarks via benchmarks/run.sh
```

Pytest markers: `unit`, `contract`, `integration`, `concurrency`, `stress`.

---

## 5. Git and workflow

- Trunk-based. One short-lived branch per phase: `phase/NN-name`.
- Conventional commits: `feat(core): ...`, `test: ...`, `fix: ...`, `docs: ...`.
- Squash-merge to `main` after CI passes. Tag at the end of each phase (`v0.N`).
- Write an ADR (`docs/adr/NNNN-title.md`) **at the moment of decision**. Required ADRs: no-Lua, storage contract, time source, dependency vs middleware, strict sliding-log variant, failure policy, polling vs SSE/WebSocket, identity trust.
- Every fixed bug gets a regression test.

**When to run what:** unit, contract, API on every commit. Integration and concurrency on every PR (Redis service container). E2E compose smoke on PRs touching infra. Stress and k6 manually or nightly.

---

## 6. Phase overview

| Phase | Name | Gate to start |
|---|---|---|
| 0 | Assessment, tooling, ADRs | none |
| 1 | Foundational contracts and in-memory storage | P0 |
| 2 | Limiter interface and Fixed Window (memory) | P1 |
| 3 | Remaining algorithms on memory | P2 |
| 4 | Redis storage, atomic primitives, concurrency harness | P3 |
| 5 | Optimistic transactions (Token Bucket, Sliding Log) | P4 |
| 6 | FastAPI integration and HTTP behavior | P5 |
| 7 | Failure handling and reliability | P6 |
| 8 | Observability | P7 |
| 9 | Stats, admin API, contract freeze | P8 |
| 10 | Distributed Compose deployment, cross-instance proof | P9 |
| 11 | Frontend dashboard | P9 (and P10 for LB integration) |
| 12 | Benchmarking | P10 |
| 13 | Documentation and hardening | all |

---

## Phase 0: Assessment, tooling, decision records

**Objective:** know what exists and establish the toolchain.
**Why first:** every later phase depends on tooling and an accurate inventory.
**Depends on:** nothing.

**Files**
- `backend/pyproject.toml`: dependencies, ruff, mypy (strict for `core/`), pytest markers.
- `Makefile`: `lint test up down bench`.
- `.github/workflows/ci.yml`: lint, type-check, test run.
- `docs/adr/0001-no-lua.md`: records the no-Lua decision and the limitations it implies.
- `.gitignore`, `.env.example`, `.pre-commit-config.yaml`.

**Steps**
1. Inventory existing files; record "exists" vs "proposed" in `docs/design.md`.
2. Create `pyproject.toml` with dev dependencies and markers.
3. Add Makefile targets and pre-commit hooks.
4. Add CI workflow running lint and tests.
5. Write ADR-0001.
6. Add one trivial smoke test so CI is green.

**Tests:** trivial smoke test.
**Verify:** `make lint && make test`; push; CI green.
**Acceptance:** CI green; inventory recorded.
**Defer:** all application code.

---

## Phase 1: Foundational contracts and in-memory storage

**Objective:** establish domain models, clock, key builder, storage contract, and memory store.
**Why now:** all algorithms and tests build on these; changing them later causes rework.
**Depends on:** P0.

**Files**
- `core/models.py`: `Policy`, `Decision`, `Subject`, with validation.
- `core/errors.py`: typed errors (`StorageUnavailable`, `ContentionExhausted`, `InvalidPolicy`, `InvalidIdentity`).
- `core/clock.py`: `Clock` protocol, `SystemClock`, `FakeClock`.
- `core/observer.py`: no-op event protocol.
- `core/key_builder.py`: collision-safe key construction.
- `core/storage/base.py`: the storage contract with atomicity documented per operation.
- `core/storage/memory.py`: in-memory implementation with conflict injection.
- `tests/conftest.py`: fixtures (`FakeClock`, store factories).
- `tests/contract/storage_contract.py`: parametrized contract suite.

**Steps**
1. Write models with validation (positive values, `window_ms >= 1000`, etc.).
2. Implement `FakeClock` (advance/set).
3. Implement key builder with kind tags, hashing, versioning; test collisions.
4. Write `storage/base.py` docstrings stating atomicity of each operation **before** implementing.
5. Implement memory store (per-key version counters for `optimistic`).
6. Run contract suite against memory.

**Risks:** a vague contract. Write the atomicity statement first.
**Tests:** model validation; key uniqueness and escaping (property tests); contract suite (memory); `FakeClock`.
**Verify:** `pytest -m "unit or contract"`.
**Acceptance:** contract suite passes; mypy strict passes on `core/`; import-boundary check shows no FastAPI/Redis imports in `core/` (except `redis_store.py` later).
**Defer:** Redis, algorithms.

---

## Phase 2: Limiter interface and Fixed Window (memory)

**Objective:** validate the `Limiter` interface with one correct algorithm.
**Why now:** one algorithm proves the interface before three more depend on it.
**Depends on:** P1.

**Files**
- `core/limiter.py`: `Limiter` ABC, `LimiterFactory` skeleton.
- `core/algorithms/fixed_window.py`.
- `tests/unit/test_fixed_window.py`.

**Steps**
1. Define the ABC and factory registry.
2. `window_id = now_ms // window_ms`; key per window.
3. Call `incr_expiring` with TTL `window_ms + grace`.
4. Allow iff `count <= limit`; `remaining = max(0, limit - count)`.
5. `reset_after_ms = window_end - now`; `retry_after_ms = reset_after_ms`.
6. Document that rejected requests still increment the counter.

**Risks:** boundary bursts (up to 2L across a window boundary). Write a test that demonstrates it and document it in `docs/algorithms.md`.
**Tests:** exactly L allowed then rejected; new window resets; boundary burst demo; `cost > 1`; expiry via `FakeClock`; invalid config rejected.
**Verify:** `pytest tests/unit/test_fixed_window.py`.
**Acceptance:** `Decision` fields correct under test; the interface needed no changes to host the algorithm.
**Defer:** other algorithms.

---

## Phase 3: Remaining algorithms on memory

**Objective:** settle semantics of all four algorithms before Redis complexity.
**Depends on:** P2.

**Files**
- `core/algorithms/token_bucket.py`, `sliding_window_log.py`, `sliding_window_counter.py`.
- Factory registry in `core/limiter.py`.
- `docs/algorithms.md` (data model, operations, complexity, accuracy, memory, edge cases per algorithm).
- Unit tests for each.

**Steps** (implement one at a time; merge tests before starting the next)
1. **Token bucket:** integer **milli-tokens** (no float drift); `elapsed = max(0, now - last)`; `tokens = min(C, tokens + elapsed * r)`; deny without writing.
2. **Sliding log:** unique members `"{ts}:{uuid4hex}"`; trim old entries; count; admit if `< L`.
3. **Sliding counter:** `estimate = prev * (1 - elapsed/window) + curr`; floor consistently; document the approximation versus the true sliding count.
4. Add a cross-algorithm comparison test (same traffic trace, compare allowed counts) feeding `docs/algorithms.md`.

**Risks:** float precision in the weighted estimate; clock regression (handled by `max(0, ...)`).
**Tests:** boundary and expiry cases per algorithm; same-millisecond uniqueness (log); zero-refill bucket allows exactly C; clock regression; comparison test.
**Verify:** `pytest -m unit`.
**Acceptance:** all four pass an identical parametrized interface suite; accuracy documented.
**Defer:** distributed correctness.

---

## Phase 4: Redis storage, atomic primitives, concurrency harness

**Objective:** Redis adapter for the single-atomic-op algorithms, plus the concurrency test harness.
**Depends on:** P3.

**Files**
- `core/storage/redis_store.py`: the only file importing `redis`.
- `docker-compose.yml`: Redis only (healthcheck, `noeviction`, localhost-bound port for tests).
- `tests/integration/`, `tests/concurrency/harness.py` (N-task `asyncio.gather` runner with fixed clock).
- Contract suite parametrized to also run on Redis.

**Steps**
1. Async connection pool with explicit timeouts.
2. `now_ms` via Redis `TIME` (with an app-clock option for tests).
3. `incr_expiring` as `MULTI(INCR, PEXPIRE)/EXEC` (explicitly transactional).
4. `incr_expiring_peek`, `decr`.
5. Run the contract suite on Redis.
6. Run Fixed Window and Sliding Counter on Redis.
7. Write concurrency tests.

**Risks**
- `redis-py` pipelines are transactional by default; be explicit.
- Disable client auto-retry for non-idempotent commands (retrying `INCR` double-counts).
- Redis eviction would silently weaken limits; use `noeviction`.

**Tests:** contract on Redis; N=200 at L=50 on one key gives **exactly 50 allowed** (fixed window); sliding counter gives **<= L**; TTL always present after `incr_expiring` (assert via `PTTL`).
**Verify:** `docker compose up -d redis && pytest -m "integration or concurrency"`.
**Acceptance:** contract green on both stores; fixed-window invariant holds across 20 repeated runs.
**Defer:** WATCH-based algorithms.

---

## Phase 5: Optimistic transactions (Token Bucket, Sliding Log on Redis)

**Objective:** correct conditional decisions via `WATCH` + `MULTI/EXEC` + bounded retry.
**Depends on:** P4.

**Files**
- `redis_store.py`: add `optimistic` / `Txn`.
- `memory.py`: version-based conflicts with injection hook.
- `core/storage/retry.py`: bounded retries with jittered backoff.
- Concurrency tests.

**Steps**
1. `WATCH` state keys on a **dedicated connection** held for the whole attempt.
2. Read state, compute decision locally.
3. If allowed: `MULTI`, queue writes (+ `PEXPIRE`), `EXEC`.
4. `None` result or `WatchError` means conflict: re-read, recompute, retry.
5. Max 5 attempts, jitter 1-5 ms. On exhaustion raise `ContentionExhausted`.
6. Denied token-bucket requests commit nothing (lazy refill), so they cause no conflicts.
7. Emit `on_txn_conflict` / `on_txn_retry_exhausted` through the observer.

**Risks:** `WATCH` is per-connection; hot-key contention grows retries with concurrency; exhaustion semantics are resolved by the failure policy (P7).
**Tests:** deterministic conflict injection (first k commits fail, result still correct); retry exhaustion raises the typed error; N=200 at L=50 gives exactly 50 allowed for both algorithms with a fixed clock; tokens never negative.
**Verify:** `pytest -m concurrency` repeated 20 times.
**Acceptance:** invariants hold; conflict/retry events reported; strict-limit claim documented **only** for the WATCH variant.
**Defer:** metrics exposition, failure policy.

**Note on plain `MULTI/EXEC` for sliding log:** `ZREMRANGEBYSCORE + ZADD + ZCARD` adds before learning the count. Compensating with `ZREM` is a second non-atomic step: safety holds (admitted <= L) but false rejections occur and a crash leaves a phantom entry. Treat as an optional "fast mode" stretch goal, never the reference.

---

## Phase 6: FastAPI integration and HTTP behavior

**Objective:** expose limiting through HTTP with correct headers, errors, and identity handling.
**Depends on:** P5.

**Files**
- `app/main.py`: app factory; lifespan creates/closes the Redis pool.
- `app/config.py`: pydantic-settings.
- `app/policies.py` and `backend/policies.yaml`: route-to-policy registry (data, not code).
- `app/api/dependencies.py`: `rate_limit(policy_id)` dependency.
- `app/api/identity.py`: identity extraction and proxy trust.
- `app/api/errors.py`: `application/problem+json` handlers.
- `app/api/schemas.py`: HTTP DTOs (not used by `core/`).
- `app/api/routes_demo.py`, `app/api/routes_health.py`.
- `app/middleware/observability.py`: request ID, `X-Served-By`, `Server-Timing`.
- `backend/Dockerfile`: slim, non-root, healthcheck.
- `tests/api/`.

**Steps**
1. Settings and policy loading with validation.
2. Identity extraction with trusted-proxy rule.
3. Dependency: build key, call limiter, set headers on allowed responses, raise typed exception on deny.
4. Error handlers producing the 429 body.
5. Demo and health routes.
6. Middleware for request context.

**Risks:** header spoofing; `Retry-After` rounding (use ceil); headers must also appear on 429 responses.
**Tests:** `httpx.AsyncClient` with memory store override: 429 body shape, headers, per-route policy differences, identity precedence, spoofed `X-Forwarded-For` ignored from untrusted peers, malformed identity gives 400.
**Verify:** `pytest tests/api`; manual `curl -i` loop against `uvicorn`.
**Acceptance:** all four algorithms selectable per route by config alone; OpenAPI is accurate.
**Defer:** admin, metrics, failure handling.

---

## Phase 7: Failure handling and reliability

**Objective:** defined behavior when Redis is unavailable or slow.
**Depends on:** P6.

**Files**
- `core/failure_policy.py`, `core/circuit_breaker.py`.
- `redis_store.py`: timeouts.
- Unit and integration tests.

**Steps**
1. Timeouts (connect ~100 ms, socket ~100 ms, configurable).
2. Breaker states: closed, open, half-open (N consecutive failures opens; cooldown permits a probe; success closes).
3. Policy resolution per `Policy.on_failure`:
   - **open:** allow, `degraded=true`, header `X-RateLimit-Status: degraded`.
   - **closed:** `503` with `Retry-After`.
   - **local_fallback:** per-instance memory limiter, explicitly **not** global (effective limit up to N x L).
4. Contention exhaustion follows `on_contention` (default reject) and is counted separately from outages.

**Risks:** local fallback mistaken for global enforcement; breaker flapping.
**Tests:** fault-injecting store wrapper (raise/timeout on demand); breaker transitions with `FakeClock`; recovery; each policy mode; exhaustion path.
**Verify:** unit tests plus `docker compose pause redis` / `unpause` integration test.
**Acceptance:** documented behavior matrix passes; recovery without app restart.
**Defer:** metrics for these events (observer hooks already fire).

---

## Phase 8: Observability

**Objective:** low-cardinality Prometheus metrics for all subsystems.
**Depends on:** P7.

**Files**
- `app/metrics/prometheus.py`: implements `core.observer`, registry, `/metrics` route.
- `infra/prometheus/prometheus.yml`.
- Compose service for Prometheus.

**Metrics**

| Name | Type | Labels |
|---|---|---|
| `rl_decisions_total` | counter | policy, algorithm, decision, reason |
| `rl_decision_duration_seconds` | histogram | algorithm |
| `http_request_duration_seconds` | histogram | method, route_template, status_class |
| `rl_storage_op_duration_seconds` | histogram | op |
| `rl_storage_errors_total` | counter | op, error_type |
| `rl_txn_conflicts_total` | counter | algorithm |
| `rl_txn_attempts` | histogram | algorithm |
| `rl_txn_retry_exhausted_total` | counter | algorithm |
| `rl_failure_policy_applied_total` | counter | mode |
| `rl_breaker_state` | gauge | state |
| `rl_storage_up` | gauge | none |
| `rl_log_entries_seen` | histogram | algorithm=sliding_log |

**Rules:** never use user IDs, IPs, request IDs, or raw paths as labels. Use route **templates**. Single-worker containers avoid multiprocess-mode complexity. Per-client numbers belong in the stats store (P9), not Prometheus.

**Tests:** scrape `/metrics` after traffic and assert series exist; a label-allowlist test that fails on unknown labels.
**Verify:** `curl :8000/metrics`; Prometheus UI at `localhost:9090`.
**Acceptance:** every metric above emitted in at least one test.
**Defer:** dashboards of these metrics.

---

## Phase 9: Stats, admin API, contract freeze

**Objective:** administrative and statistics endpoints; freeze the API contract.
**Depends on:** P8.

**Files**
- `app/services/stats.py`: bounded per-second counters per policy (TTL ~5 min); capped top-N clients using truncated hashed IDs.
- `app/api/routes_admin.py`, `app/api/auth.py` (bearer token from env, constant-time compare).
- Schema updates; `docs/openapi.json` snapshot.

**Endpoints**

| Method / path | Auth | Purpose | Status codes |
|---|---|---|---|
| `GET /healthz` | none | Liveness (process only) | 200 |
| `GET /readyz` | none | Readiness: initialized + Redis PING (cached ~1 s) | 200 / 503 |
| `GET /health/dependencies` | admin | Redis latency, breaker state (no keys, no connection strings) | 200 |
| `GET /metrics` | internal network only (not routed by Nginx) | Prometheus | 200 |
| `GET /api/v1/demo/ping` | none | Default policy | 200 / 429 / 503 |
| `GET /api/v1/demo/strict` | none | Low-limit policy | 200 / 429 |
| `GET /api/v1/demo/limited/{algorithm}` | none | One policy per algorithm | 200 / 429 / 404 |
| `GET /api/v1/policies` | none | Read-only effective policies | 200 |
| `PUT /api/v1/admin/policies/{id}` | admin bearer | Update demo policy within bounded ranges | 200 / 401 / 403 / 404 / 422 |
| `GET /api/v1/admin/stats?policy_id=&window_s=60` | admin | Per-second allowed/rejected series, top-N clients | 200 |
| `POST /api/v1/admin/reset` | admin | Reset by exact computed key (never `SCAN`) | 204 |

**429 body** (`application/problem+json`): `type, title, status, detail, policy_id, retry_after_ms, request_id`.
**Headers:** `Retry-After` (ceil seconds, on 429/503), `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (delta seconds), `X-RateLimit-Status: degraded` when applicable, `X-Served-By`, `Server-Timing: limiter;dur=...`. Remaining/reset are exact for fixed window, log, and bucket; flagged as estimated for the sliding counter.

**Steps**
1. Stats service with bounded memory and TTL.
2. Admin auth dependency.
3. Admin routes; runtime policy overrides stored in Redis with a version and a 2 s local cache, limited to demo policies with bounded ranges.
4. Generate and commit `docs/openapi.json`; add CI diff.

**Risks:** unauthenticated policy mutation; unbounded stats memory.
**Tests:** 401/403 paths; validation bounds; stats TTL; overrides propagate across two app instances.
**Verify:** `pytest tests/api`; CI OpenAPI diff.
**Acceptance:** OpenAPI snapshot committed. **Frontend work may start only after this.**
**Defer:** UI.

---

## Phase 10: Distributed Compose deployment and cross-instance proof

**Objective:** prove replicas share rate-limit state through one Redis.
**Depends on:** P9.

**Files**
- `docker-compose.yml` (full), `infra/nginx/nginx.conf`, `.dockerignore`.
- `tests/e2e/test_cross_instance.py`, `docs/operations.md`.

**Steps**
1. Services: `redis`, `api1..3`, `lb`, `prometheus` (frontend added in P11).
2. Healthchecks on all; `depends_on: condition: service_healthy`.
3. Two networks: `edge` and `backend` (Redis only on `backend`).
4. `stop_grace_period: 15s`; per-instance `INSTANCE_ID`.
5. Nginx: explicit upstream (api1..3), round-robin, **`proxy_next_upstream off`** (a retried request would double-count), overwrite `X-Real-IP`, do not expose `/metrics`.

**Proof of shared state**
- Send L+K requests through the LB; read `X-Served-By`.
- Assert (a) at least 2 instances served requests and (b) total allowed equals **exactly L**.
- **Negative control:** rerun with `STORAGE=memory`; allowed should be roughly 3 x L. This contrast is the key interview demonstration.

**Scope statement:** this proves multiple API replicas sharing **one** Redis. It is **not** a highly available Redis. Redis is a single point of failure; the failure policy only manages its impact.

**Risks:** fixed-window boundary crossing mid-test (use a long window or assert on reported reset time).
**Verify:** `docker compose up -d --build && pytest tests/e2e`.
**Acceptance:** proof and negative control pass; `docker compose down` shuts down cleanly.
**Defer:** UI.

---

## Phase 11: Frontend dashboard

**Objective:** demonstrate backend behavior in a React + TypeScript UI.
**Depends on:** P9 (contract freeze); P10 for LB integration.

**Files** (`frontend/`)
- `package.json`, `vite.config.ts`, `tsconfig.json`, ESLint config, `Dockerfile`, `nginx.conf` (static serving).
- `src/App.tsx`, `src/api/client.ts` (typed from `docs/openapi.json`), `src/hooks/usePolling.ts`, `src/types/`.
- `src/components/`: `PolicyPanel`, `TrafficGenerator`, `LiveCounters`, `LatencyChart`, `HealthBadge`, `AlgorithmCompare`.
- `src/__tests__/` (vitest + React Testing Library + MSW).

**Decisions**
- **Polling (1 s)** with `AbortController` and exponential backoff. Data is one-way and 1 s granularity suffices. SSE adds long-lived connections through Nginx (buffering, reconnect handling) for no real gain; WebSockets are unnecessary because the client does not push. Revisit only for sub-second updates.
- Traffic generator: browser `fetch` with bounded concurrency and a demo client ID (honored only with `DEMO_MODE`). Use k6 for real load.
- Admin token held **in memory only**.
- Serve dashboard through Nginx on one origin (no CORS).

**Views:** selected policy and algorithm; editable limits (admin token required); live allowed/rejected counts; client-observed latency vs server `Server-Timing` limiter latency; health badge; side-by-side algorithm comparison (same burst against the four per-algorithm demo routes).
**States:** every component handles loading, empty, error, and disconnected (N consecutive failed polls).

**Tests:** vitest + MSW for each state; type-check in CI.
**Verify:** `npm run lint && npm run typecheck && npm test && npm run build`; manual run through the LB.
**Acceptance:** all four UI states tested; dashboard works through Nginx.
**Defer:** nothing visual beyond this.

---

## Phase 12: Benchmarking

**Objective:** reproducible, honest performance analysis with k6.
**Depends on:** P10.

**Files**
- `benchmarks/k6/*.js` (scenario scripts), `benchmarks/run.sh` (captures environment and runs).
- `benchmarks/results/<date>-<sha>/`: raw k6 JSON, `meta.json`, summary.
- `docs/benchmarks.md`.

**Scenarios**
1. Single instance vs multiple instances.
2. All four algorithms at comparable configuration (bucket capacity = L, refill = L/window; note they are not semantically identical).
3. Varying arrival rates and client counts.
4. Low vs high contention on one key.
5. Requests spread across many keys.
6. Redis resource pressure (CPU limits).
7. Token-bucket optimistic-locking conflicts.
8. Sliding-log storage growth (large L).
9. Redis stop/restart (if feasible).

**Method:** constant-arrival-rate executors (avoid coordinated omission); warm-up; 3+ runs; report median and spread.
**Measure:** throughput, p50/p95/p99 latency, error rate, rejection rate, Redis `INFO commandstats/memory`, `docker stats`, retry counts from Prometheus.
**Reproducibility:** `run.sh` writes `meta.json` (git SHA, CPU/RAM, Docker/Redis/k6 versions, scenario, env vars) beside raw output.
**Interpretation:** laptop numbers are comparative only (k6 shares host CPU). Do not extrapolate to production. Do not publish numbers without raw files.

**Acceptance:** every figure in `docs/benchmarks.md` traces to a committed raw result.

---

## Phase 13: Documentation and hardening

**Objective:** finish documentation, fix remaining bugs, release.
**Depends on:** all previous phases.

**Files:** `README.md` (quick start, architecture diagram, measured results), `docs/design.md`, `docs/algorithms.md`, `docs/operations.md`, remaining ADRs.

**Steps**
1. Document each guarantee together with its limits.
2. Fix bugs found; add a regression test for each.
3. Record out-of-scope items in `docs/design.md`: Redis HA/Cluster, Lua, distributed locks, Kubernetes, tracing.
4. Final CI pass; tag `v1.0`.

**Acceptance:** the completion checklist (section 10) is fully ticked.

---

## 7. Per-algorithm correctness reference

**Pipeline vs transaction:** a pipeline (`transaction=False`) only batches round trips; other clients can interleave. `MULTI/EXEC` runs queued commands contiguously, but you cannot read a result inside it and branch. Conditional logic needs `WATCH` (check, then write only if unchanged) or a design that uses the atomic command's own return value.

| Algorithm | Redis design | Admitted <= L | False rejections | Notes |
|---|---|---|---|---|
| Fixed window | `MULTI(INCR, PEXPIRE)/EXEC`; allow iff returned count <= L | **Exact** | None | Boundary bursts up to 2L; rejected requests still count |
| Sliding log (strict) | `WATCH` key; trim; `ZCARD`; if `< L` then `MULTI ZADD PEXPIRE EXEC`; retry on conflict | **Exact** | None | Contention cost on hot keys; memory O(L) per client |
| Sliding counter | `MULTI(INCR curr, PEXPIRE, GET prev)/EXEC`; estimate locally; `DECR` if over | **By estimate** | Possible | Crash between INCR and DECR leaves phantom increment up to one window; approximates the true sliding count |
| Token bucket | `WATCH`; `HMGET`; refill; if `>= cost` then `MULTI HSET PEXPIRE EXEC`; else deny with no write | **Exact vs refill bound** | None (except exhaustion) | Tokens never negative; denial may be stale a moment later |

**Unavoidable limitations without Lua:** the clock read is separate from the transaction, and compensation steps are not atomic with the original increment. A future Lua version would remove both.

**Concurrency invariants** (N requests, limit L, one key, frozen window):
- Fixed window, sliding log (WATCH), token bucket (zero refill): allow **exactly min(N, L)**.
- Sliding counter: allow **<= L** and report the observed shortfall.
- Fixed-window boundary tests assert the **documented 2L worst case**, not L.
- Deterministic tests must never be flaky. Statistical tests get their own marker and tolerance.

---

## 8. Deployment and failure reference

**Compose:** `redis` (7.x, `maxmemory 256mb`, `noeviction`, healthcheck `redis-cli ping`), `api1..3` (same image, `INSTANCE_ID`, Python healthcheck on `/healthz`), `lb` (Nginx), `frontend` (static), `prometheus` (explicit scrape targets `api1:8000`, `api2:8000`, `api3:8000`).

**Startup/shutdown:** lifespan opens and closes the Redis pool; uvicorn handles SIGTERM; readiness gates traffic.

**Failure matrix:** Redis down or slow gives timeout, then breaker opens, then policy (`open` allows and marks degraded; `closed` returns 503; `local_fallback` applies a per-instance limit that is **not** global). A half-open probe restores normal operation. Contention exhaustion follows `on_contention`.

---

## 9. Risk register

| # | Risk | Likelihood / Impact | Detection | Mitigation | Phase |
|---|---|---|---|---|---|
| 1 | Concurrency bug breaking the limit | M / H | invariant tests, repeated runs | exact invariants, conflict injection | 4-5 |
| 2 | Claiming atomicity `MULTI/EXEC` does not give | H / H | design review against section 7 | document per-algorithm guarantees | 5, 13 |
| 3 | Redis outage causing outage or silent bypass | M / H | fault tests | explicit policies, `degraded` flag | 7 |
| 4 | Local fallback mistaken for global enforcement | M / M | docs and tests assert `degraded` | label plus metric | 7 |
| 5 | Misleading algorithm comparison | H / M | config parity review | equivalent configs, caveats, raw data | 12 |
| 6 | Misleading benchmarks (shared CPU, no warm-up) | H / M | `meta.json` review, run variance | constant-arrival, repeats, caveats | 12 |
| 7 | Key collisions | L / H | property tests | typed, hashed, versioned keys | 1 |
| 8 | Metric cardinality explosion | M / M | label-allowlist test | bounded labels only | 8 |
| 9 | Retried request double-counted (client retry, Nginx) | M / M | e2e count test | no auto-retry on INCR, `proxy_next_upstream off` | 4, 10 |
| 10 | Clock skew across instances | M / M | skew test with app clocks | Redis `TIME` as authority | 4 |
| 11 | Redis eviction weakening limits | M / M | config check | `noeviction`, memory alert | 4, 10 |
| 12 | Sliding-log memory growth | M / M | growth benchmark | cap L, document cost | 12 |
| 13 | Header spoofing | M / H | API tests | trusted-proxy rule | 6 |
| 14 | Scope creep before backend is stable | H / M | phase gates | frontend only after P9 | 9-11 |

---

## 10. Milestones and completion checklist

**Minimum viable project (interview-ready core):** Phases 0-7, a minimal Phase 10 (Redis + 3 API instances + Nginx + cross-instance proof with negative control), and one measured k6 run. This delivers four algorithms on Redis, HTTP behavior, failure policy, and the distributed proof.

**Full completion checklist**
- [ ] All 13 phases' acceptance criteria met
- [ ] Feature coverage matrix has no unproven row
- [ ] Every guarantee and limitation documented
- [ ] Benchmark claims trace to committed raw files
- [ ] Dashboard loading/empty/error/disconnected states tested
- [ ] CI green (ruff, mypy, import-boundary, unit/contract/API, Redis integration + concurrency, frontend lint/typecheck/test/build, Docker build, compose smoke, OpenAPI diff)
- [ ] `v1.0` tagged

---

## 11. Feature coverage matrix

| Requirement | Phase | Main files | Validating tests | Acceptance |
|---|---|---|---|---|
| Four algorithms behind one interface | 2-3 | `limiter.py`, `algorithms/*` | parametrized interface tests | all pass the same suite |
| Algorithm documentation | 3, 13 | `docs/algorithms.md` | doc review | each algorithm documented per section 7 |
| Atomic strategy per algorithm | 4-5 | `redis_store.py`, `retry.py` | concurrency suite | invariants hold over 20 runs |
| Conflicts, retries, exhaustion | 5 | `retry.py`, `memory.py` hooks | conflict-injection tests | typed error plus observer events |
| No Lua; limits documented | 0, 13 | ADR-0001, `design.md` | n/a | ADR present |
| Interfaces, config validation, pluggable storage | 1 | `models.py`, `storage/base.py` | contract and validation tests | contract green on both stores |
| Key builder, collisions | 1 | `key_builder.py` | uniqueness/escaping tests | no collisions in property tests |
| Identity, spoofing, per-route policies | 6 | `identity.py`, `policies.py` | API tests | spoofed headers ignored |
| Endpoints, 429, headers, errors | 6, 9 | `api/*` | API tests | OpenAPI snapshot matches |
| Admin protection, health separation | 6, 9 | `auth.py`, `routes_health.py` | 401/403, readiness tests | no unauthenticated mutation |
| Failure policies, timeouts, breaker, recovery | 7 | `failure_policy.py`, `circuit_breaker.py` | fault tests, Redis pause | behavior matrix passes |
| Metrics and cardinality | 8 | `metrics/prometheus.py` | scrape and allowlist tests | all metrics emitted |
| Dashboard and states | 11 | `frontend/src/*` | vitest/MSW | four states tested |
| Polling vs SSE/WebSocket decision | 11 | ADR | n/a | decision recorded |
| Compose and shared-state proof | 10 | `docker-compose.yml`, `nginx.conf` | e2e plus negative control | exactly L across instances |
| Testing strategy and invariants | 1-10 | `tests/*` | CI runs all classes | CI matrix green |
| k6 benchmarks and methodology | 12 | `benchmarks/*` | n/a | results trace to raw files |
| Deferred / out of scope | n/a | n/a | n/a | Redis HA/Cluster, Lua, distributed locks, Kubernetes, tracing, each adds complexity beyond the brief |

Nothing is marked complete until its acceptance criterion passes.

---

## 12. Final repository structure

```
distributed-rate-limiter/
├── AGENTS.md
├── backend/
│   ├── pyproject.toml                  # P0
│   ├── policies.yaml                   # P6
│   ├── Dockerfile                      # P6
│   ├── app/
│   │   ├── main.py                     # P6
│   │   ├── config.py                   # P6
│   │   ├── policies.py                 # P6
│   │   ├── api/
│   │   │   ├── dependencies.py         # P6
│   │   │   ├── identity.py             # P6
│   │   │   ├── auth.py                 # P9
│   │   │   ├── errors.py               # P6
│   │   │   ├── schemas.py              # P6
│   │   │   ├── routes_demo.py          # P6
│   │   │   ├── routes_health.py        # P6
│   │   │   └── routes_admin.py         # P9
│   │   ├── services/
│   │   │   └── stats.py                # P9
│   │   ├── core/                       # no FastAPI/Prometheus imports
│   │   │   ├── models.py               # P1
│   │   │   ├── errors.py               # P1
│   │   │   ├── clock.py                # P1
│   │   │   ├── observer.py             # P1
│   │   │   ├── key_builder.py          # P1
│   │   │   ├── limiter.py              # P2
│   │   │   ├── failure_policy.py       # P7
│   │   │   ├── circuit_breaker.py      # P7
│   │   │   ├── algorithms/
│   │   │   │   ├── fixed_window.py             # P2
│   │   │   │   ├── token_bucket.py             # P3 (Redis P5)
│   │   │   │   ├── sliding_window_log.py       # P3 (Redis P5)
│   │   │   │   └── sliding_window_counter.py   # P3 (Redis P4)
│   │   │   └── storage/
│   │   │       ├── base.py             # P1
│   │   │       ├── memory.py           # P1
│   │   │       ├── redis_store.py      # P4/P5
│   │   │       └── retry.py            # P5
│   │   ├── middleware/
│   │   │   └── observability.py        # P6/P8
│   │   └── metrics/
│   │       └── prometheus.py           # P8
│   └── tests/
│       ├── conftest.py                 # P1
│       ├── unit/                       # P2-P3, P7
│       ├── contract/storage_contract.py # P1/P4
│       ├── integration/                # P4+
│       ├── concurrency/                # P4-P5
│       ├── api/                        # P6, P9
│       └── e2e/test_cross_instance.py  # P10
├── frontend/                           # P11
│   ├── package.json, vite.config.ts, tsconfig.json, Dockerfile, nginx.conf
│   └── src/ (App.tsx, api/, hooks/, components/, types/, __tests__/)
├── infra/
│   ├── nginx/nginx.conf                # P10
│   └── prometheus/prometheus.yml       # P8
├── benchmarks/
│   ├── k6/                             # P12
│   ├── run.sh                          # P12
│   └── results/                        # P12
├── docs/
│   ├── design.md, algorithms.md, benchmarks.md, operations.md
│   ├── openapi.json                    # P9
│   └── adr/
├── .github/workflows/ci.yml            # P0, extended each phase
├── docker-compose.yml                  # P4 (Redis) -> P10 (full)
├── Makefile, .env.example, .gitignore, .dockerignore, README.md
```

---

## 13. Instructions for the agent: how to work

1. Read this file, then inspect the repository. Report which files exist versus are proposed.
2. Identify the current phase (the first phase whose acceptance criteria do not pass).
3. Implement only that phase's scope. Respect "Defer" lists.
4. Write tests alongside code. Run the phase's verify commands.
5. Do not mark a phase done until every acceptance criterion passes.
6. When a design decision is made, add or update an ADR.
7. If the plan conflicts with reality (library behavior, version differences), stop, document the discrepancy, and propose a change rather than silently deviating.
8. **Immediate next step if starting fresh:** Phase 0, then Phase 1, beginning with the `storage/base.py` atomicity docstrings.