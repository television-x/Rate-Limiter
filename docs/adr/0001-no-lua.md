# ADR-0001: No Lua Scripts in Initial Implementation

## Status

Accepted

## Date

2026-10-12

## Context

Redis supports server-side Lua scripting via `EVAL` / `EVALSHA`. A Lua script
executes atomically on the Redis server: no other command can interleave. This
would let us read state, branch on the result, and write — all in one atomic
step.

Our rate limiter needs conditional decisions: "read the current count; if under
the limit, increment; otherwise deny." Without Lua, we cannot make this
conditional check-and-update truly atomic in a single command.

## Decision

**We will not use Lua scripts in the initial implementation.**

Instead, we use two strategies:

1. **`MULTI/EXEC` (unconditional transactions)** for algorithms where the
   decision can be derived from the command's return value after execution
   (Fixed Window, Sliding Window Counter). The increment happens
   unconditionally; the decision is computed from the returned count.

2. **`WATCH` + `MULTI/EXEC` (optimistic concurrency)** for algorithms that
   require a conditional decision before writing (Token Bucket, Sliding Window
   Log). If the watched keys change between the read and the transaction, the
   transaction fails and is retried with bounded attempts and jittered backoff.

## Consequences

### Limitations accepted

- **Clock read is separate from the transaction.** The Redis `TIME` call and
  the subsequent transaction are not atomic. Under extreme clock drift (Redis
  `TIME` changes between read and write), a small inaccuracy is possible. This
  is acceptable for our use case.

- **Compensation steps are not atomic with the original operation.** The
  Sliding Window Counter uses `INCR` followed by a potential `DECR`
  (compensation). A crash between these leaves a phantom increment that
  over-counts for at most one window. This is documented and accepted.

- **Optimistic concurrency has contention cost.** Under high concurrency on a
  single key, `WATCH`-based algorithms may require multiple retries. After a
  bounded number of attempts, we raise `ContentionExhausted` and apply the
  failure policy.

- **Rejected requests still increment in Fixed Window.** Because the increment
  is unconditional, denied requests consume counter space. This is standard
  fixed-window behavior and is documented.

### Benefits

- **Simpler deployment.** No Lua scripts to manage, version, or debug on the
  Redis server.

- **Clearer correctness reasoning.** Each algorithm's atomicity guarantees are
  documented individually rather than hidden inside opaque Lua.

- **Easier testing.** Python-side logic is directly testable. Memory store
  tests validate semantics; Redis tests validate concurrency.

## Future Optimization

A Lua-based implementation could:
- Eliminate the clock-read gap (read `TIME` inside the script).
- Remove compensation steps (branch inside the script).
- Reduce `WATCH` retries to zero (no optimistic concurrency needed).
- Slightly improve throughput by reducing round trips.

This is a valid future enhancement, not a current requirement.
