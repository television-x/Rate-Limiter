# Rate Limiting Algorithms

This document details the data model, storage operations, time/space complexity, accuracy guarantees, and edge case behaviors for all four rate-limiting algorithms implemented in the distributed rate limiter.

---

## 1. Overview & Comparison Matrix

| Property | Fixed Window | Token Bucket | Sliding Window Log | Sliding Window Counter |
|---|---|---|---|---|
| **Accuracy** | Exact (within fixed window) | Exact (within refill resolution) | Exact (strict sliding window) | Estimated (weighted average) |
| **Storage Primitive** | `incr_expiring` (`MULTI/EXEC`) | `optimistic` (`WATCH/MULTI/EXEC`) | `optimistic` (`WATCH/MULTI/EXEC`) | `incr_expiring_peek` + compensation `decr` |
| **Time Complexity** | $O(1)$ | $O(1)$ | $O(\log N + M)$ where $N$ = entries in window | $O(1)$ |
| **Space Complexity** | $O(1)$ per key | $O(1)$ per key | $O(L)$ where $L$ is the limit | $O(1)$ (2 counter keys) |
| **Atomic Boundary** | Single atomic `INCR` + `PEXPIRE` | Atomic conditional commit (`WATCH`) | Atomic conditional commit (`WATCH`) | Incremental with peek; separate non-atomic compensation |
| **Boundary Burst Vulnerability** | **Yes** (up to $2L$ across window boundary) | **No** (capped strictly by bucket capacity $C$) | **No** (strictly enforced across any sliding interval) | **Mitigated** (bounded by interpolation) |

---

## 2. Fixed Window

### 2.1 Concept & Data Model
Divides time into contiguous fixed-size windows of duration $W$:
$$\text{window\_id} = \lfloor \frac{\text{now\_ms}}{W} \rfloor$$
Counter key: `rl:v1:{<policy_id>|<kind>:<identity_hash>}:fixed_window:<window_id>`

### 2.2 Storage Operations
- Calls `incr_expiring(key, cost, ttl_ms=W + grace)`.
- If $\text{count} \le L$: Allowed. $\text{remaining} = \max(0, L - \text{count})$.
- If $\text{count} > L$: Denied. $\text{remaining} = 0$, $\text{retry\_after\_ms} = \text{window\_end} - \text{now}$.

### 2.3 Edge Cases & Limitations
- **2L Boundary Burst Anomaly:** A client sending $L$ requests at $t = W - \epsilon$ and another $L$ requests at $t = W + \epsilon$ has all $2L$ requests admitted within an elapsed interval of $2\epsilon$.
- **Counter Poisoning:** Rejected requests increment the counter, prolonging the saturated count until window reset.

---

## 3. Token Bucket

### 3.1 Concept & Data Model
Maintains a bucket with maximum capacity $C$ and continuous refill rate $r$ tokens/second.
Key: `rl:v1:{<policy_id>|<kind>:<identity_hash>}:token_bucket` (Redis Hash)
Fields:
- `tokens_milli`: Integer milli-tokens currently in bucket ($1 \text{ token} = 1000 \text{ milli-tokens}$).
- `last_refill_ms`: Timestamp in epoch-milliseconds of previous check.

### 3.2 Refill Arithmetic & Drift Protection
To eliminate floating-point precision drift across repeated additions:
$$\text{elapsed\_ms} = \max(0, \text{now\_ms} - \text{last\_refill\_ms})$$
$$\text{refilled\_milli} = \lfloor \text{elapsed\_ms} \times \frac{r \times 1000}{1000} \rfloor$$
$$\text{tokens\_milli} = \min(C \times 1000, \text{stored\_milli} + \text{refilled\_milli})$$

### 3.3 Storage Operations & Conflict Minimization
- Read state under `WATCH`.
- If $\text{tokens\_milli} \ge \text{cost} \times 1000$: Deduct tokens, write updated state inside `MULTI/EXEC`, and commit.
- If insufficient tokens: **Denied without writing**. Denied checks produce **zero storage conflicts** for concurrent requests.

### 3.4 Edge Cases
- **Clock Regression:** Handled by clamping $\text{elapsed\_ms} = \max(0, \text{now\_ms} - \text{last\_ms})$. Clock skews do not generate phantom tokens.
- **High Concurrency Contention:** Multiple admitted requests compete for the optimistic transaction and resolve through bounded retries with jitter.

---

## 4. Sliding Window Log

### 4.1 Concept & Data Model
Stores individual request occurrences in a Redis Sorted Set (ZSET).
Key: `rl:v1:{<policy_id>|<kind>:<identity_hash>}:sliding_log`
- Score: Epoch timestamp in milliseconds ($\text{now\_ms}$).
- Member: `"{now_ms}:{uuid4hex}"`. The UUID suffix guarantees that multiple requests within the identical millisecond receive distinct entries.

### 4.2 Storage Operations
- Begin optimistic transaction watching the key.
- Fetch active count: `ZRANGEBYSCORE(window_start, now_ms)`.
- If $\text{current\_count} + \text{cost} \le L$:
  - Prune expired entries: `ZREMRANGEBYSCORE(0, window_start - 1)`.
  - Append new entries with unique UUIDs: `ZADD`.
  - Set TTL: `PEXPIRE(window_ms + grace)`.
  - Commit transaction.
- If over limit: **Denied without writing**. Oldest timestamp in log determines precise $\text{retry\_after\_ms}$.

### 4.3 Edge Cases & Limitations
- **Memory Consumption:** Storing $L$ UUID entries per active subject requires $O(L)$ space. Impractical for high limits (e.g. $100,000 \text{ req/min}$).
- **Exactness:** The most mathematically precise algorithm. Completely eliminates boundary bursts.

---

## 5. Sliding Window Counter

### 5.1 Concept & Data Model
Approximates the sliding count by interpolating between two adjacent fixed windows.
- Keys: Current window `curr_key` and previous window `prev_key`.
- Key format: `rl:v1:{<policy_id>|<kind>:<identity_hash>}:sliding_counter:<window_id>`

### 5.2 Interpolation Formula
$$\text{elapsed\_ms} = \text{now\_ms} - (\text{window\_id} \times W)$$
$$\text{weight} = 1.0 - \frac{\text{elapsed\_ms}}{W}$$
$$\text{estimate} = \lfloor \text{prev\_count} \times \text{weight} \rfloor + \text{curr\_count}$$

Accuracy is flagged as `Accuracy.ESTIMATED`.

### 5.3 Storage Operations & Compensation Mechanism
- Executes `incr_expiring_peek(curr_key, by=cost, ttl=2W + grace, peek_key=prev_key)`.
- Computes weighted estimate.
- If $\text{estimate} \le L$: Allowed.
- If $\text{estimate} > L$: Rejects request and executes compensation:
  $$\text{decr}(\text{curr\_key}, \text{cost})$$

### 5.4 Edge Cases & Crash Dynamics
- **Compensation Decoupling:** Increment and decrement are separate operations. In the event of an abrupt process crash between the increment and decrement, a phantom count remains for at most $2W$, slightly over-throttling but never admitting illegal traffic.
- **Uniformity Assumption:** The algorithm assumes requests in the previous window were uniformly distributed. If all previous requests occurred in the last millisecond of the previous window, the estimate slightly undercounts.

---

## 6. Trace Benchmark Proof

Under an identical boundary burst traffic trace ($10$ requests at $t = 9500\text{ms}$ followed immediately by $10$ requests at $t = 10100\text{ms}$, with limit $10$ per $10\text{s}$):

| Algorithm | Window 1 ($t = 9500\text{ms}$) | Window 2 ($t = 10100\text{ms}$) | Total Admitted | Boundary Burst Result |
|---|---|---|---|---|
| **Fixed Window** | 10 admitted | 10 admitted | **20 / 20** | Suffers $2L$ boundary burst |
| **Sliding Log** | 10 admitted | 0 admitted | **10 / 20** | Strictly bounded to $L$ |
| **Sliding Counter** | 10 admitted | 1 admitted | **11 / 20** | Smoothly bounded ($10\%$ decay) |
| **Token Bucket** | 10 admitted | 0 admitted | **10 / 20** | Strictly bounded to capacity $C$ |
