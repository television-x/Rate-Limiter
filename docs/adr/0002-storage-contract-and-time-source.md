# ADR 0002: Storage Contract Primitives and Time Source

## Status
Accepted

## Context
A distributed rate limiter operating across multiple application nodes requires:
1. A synchronized authoritative time source so all nodes agree on window boundaries, token refill timings, and reset durations.
2. Atomic primitives in Redis to increment counters, peek at prior window state, and manage TTLs without Lua scripts (per ADR 0001).
3. Protection against double-counting caused by automatic client-side retries over unstable networks.

## Decision

### 1. Redis `TIME` as Authoritative Clock
All rate limiters compute current time via `await storage.now_ms()`. In the Redis backend, this queries the Redis server directly using the `TIME` command.
- **Why:** System clocks across multiple instances drift (NTP synchronization skew). Relying on application node system time causes asymmetric window boundaries and race conditions.
- **Testing:** The storage adapter accepts an optional injected `Clock` (e.g. `FakeClock`) for deterministic unit and contract testing.

### 2. Transactional Primitives via MULTI/EXEC
Without Lua scripts, atomic guarantees are provided via single-pipeline `MULTI/EXEC` transactions:
- `incr_expiring`: Combines `INCRBY` and `PEXPIRE` into one atomic unit. The TTL is set unconditionally on every increment to ensure expiration idempotence.
- `incr_expiring_peek`: Combines `INCRBY`, `PEXPIRE` (on current window), and `GET` (on previous window) into a single transactional block. This guarantees that Sliding Window Counter interpolation sees a consistent snapshot.
- `decr`: Uses `DECRBY` as an atomic compensation step when a speculative increment exceeds the threshold.

### 3. Disabled Client-Side Auto-Retry on Timeout
In `redis-py`, network socket timeouts can occur after the command was received and executed by Redis, but before the response reached the client.
- **Decision:** Auto-retry is explicitly disabled (`retry_on_timeout=False`).
- **Consequence:** Retrying a non-idempotent operation like `INCR` or `DECR` would double-count quota. Failed network operations must be handled at the failure policy layer (circuit breaker / degraded mode), not via blind client retries.

## Consequences
- Redis acts as both state store and clock authority.
- Operations maintain exact quotas under high concurrency without requiring Lua scripting.
- Optimistic conditional operations requiring `WATCH` (Token Bucket, Sliding Window Log) are isolated to dedicated connections in Phase 5.
