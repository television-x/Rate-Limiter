# ADR 0003: Strict Sliding Window Log and Optimistic Locking

## Status
Accepted

## Context
Token Bucket and Sliding Window Log rate limiters require conditional state transitions:
1. Read current state (stored tokens and last refill timestamp for Token Bucket; entries within the sliding window for Sliding Window Log).
2. Compute available quota locally.
3. If allowed: mutate state (deduct tokens and update timestamp; add log entries and set TTL).
4. If denied: make no state modifications.

Per ADR 0001, Lua scripts are not used. Standard Redis `MULTI/EXEC` transactions queue commands without returning intermediate results, making conditional branching inside the transaction impossible.

For Sliding Window Log, a naive pipeline (`ZREMRANGEBYSCORE` + `ZADD` + `ZCARD`) speculatively inserts the request before learning the log size, requiring a compensating `ZREM` if `ZCARD > limit`. This is non-atomic: a network crash between `ZADD` and `ZREM` leaves phantom entries, and concurrent requests falsely reject legitimate traffic while leaving state corrupted.

## Decision

### 1. Optimistic Concurrency Control via `WATCH / MULTI / EXEC`
All conditional algorithms use Redis optimistic locking:
- A dedicated pipeline connection is used for the transaction.
- Keys are `WATCH`ed before reading state.
- Reads (`GET`, `HGETALL`, `ZRANGEBYSCORE`) execute immediately on the watched connection.
- Decision logic is evaluated in application memory.
- If allowed: `MULTI` is issued, writes are queued (`HSET`, `ZADD`, `PEXPIRE`), and `EXEC` is executed atomically.
- If denied: the transaction is aborted (`UNWATCH` / pipeline reset), releasing the connection back to the pool immediately without issuing write commands.

### 2. Bounded Retries with Jitter (`retry.py`)
If a watched key is modified by another client before `EXEC`, Redis aborts the transaction (returning `None` or raising `WatchError` in `redis-py`):
- Attempt count is bounded to a maximum of 5 attempts.
- Jittered backoff (1–5 ms random uniform) avoids synchronized retry stampedes.
- Observer events (`on_txn_conflict`, `on_txn_retry_exhausted`) track contention.
- If retries are exhausted, `ContentionExhausted` is raised to be resolved by policy (`on_contention`).

### 3. Strict Sliding Window Log Reference Variant
We adopt the WATCH-based variant as the reference implementation:
- **Correctness Guarantee:** Admitted requests are guaranteed to never exceed limit $L$ within any sliding window, and no phantom entries are ever written.
- **Lazy Refill / Denied Requests:** Denied requests perform no writes, meaning denied traffic generates zero transaction conflicts and zero lock contention.

## Consequences
- Guarantees linearizable correctness and strict limit enforcement across distributed nodes without Lua scripts.
- Connection pool sizing must account for dedicated connections held during optimistic checks.
- Hot keys experiencing severe contention back off with jitter up to 5 attempts before raising `ContentionExhausted`.

