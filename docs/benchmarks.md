# Performance Benchmarks & Empirical Analysis

This document reports the performance characteristics, throughput limits, latency distributions, and algorithmic trade-offs of the Distributed Rate Limiter. 

In strict adherence to repository guidelines (Rule 2: *Never invent results*), every metric, percentile, and count reported below directly traces to the committed raw execution result in [`benchmarks/results/2026-11-12-7d51b6c/summary.json`](../benchmarks/results/2026-11-12-7d51b6c/summary.json).

---

## 1. Environment & Methodology

### 1.1 Test Environment
As recorded in `meta.json`:
- **Hardware:** Apple M-series (8 CPU cores, `arm64`)
- **Operating System:** macOS 15.7.3 (`darwin 24.3.0`)
- **Runtime:** Python 3.12.0, FastAPI (asyncio)
- **Shared Storage:** Redis 7.x (`noeviction`, standalone)
- **Git Commit:** `7d51b6c`
- **Timestamp:** `2026-11-12T08:20:15Z`

### 1.2 Benchmark Methodology
Load tests were generated using a custom constant-arrival-rate async benchmark runner (`benchmarks/runner.py`) using `httpx`. Paced arrival times ensure that client delays or system latency do not induce coordinated omission.

Two latency perspectives were captured simultaneously for every request:
1. **Client Observed Latency:** Full round-trip time measured at the HTTP client socket level (network, request parsing, rate evaluation, handler execution, serialization, response delivery).
2. **Server-Limiter Latency:** Server-side rate limiter evaluation duration measured via high-resolution monotonic clock and emitted in the RFC standard `Server-Timing: limiter;dur=<ms>` response header.

---

## 2. Benchmark Scenarios & Measured Results

### 2.1 Scenario 1: Baseline Ping
- **Endpoint:** `/api/v1/demo/ping` (Default Fixed Window Policy, $L=60$, Window $60\text{s}$)
- **Target Rate:** 100 RPS for 5 seconds
- **Traffic Profile:** 200 distinct user IDs (`user-0` .. `user-199`)

| Metric | Measured Value |
|---|---|
| Total Requests | 500 |
| Actual Throughput | **100.0 RPS** |
| Status Codes | 200 OK: 500 (100%), 429: 0 (0%), Errors: 0 |
| Client Latency (p50 / p90 / p95 / p99) | **2.56 ms** / 3.00 ms / 3.51 ms / 17.66 ms |
| Limiter Duration (p50 / p90 / p95 / p99) | **0.58 ms** / 0.74 ms / 1.04 ms / 3.68 ms |

---

### 2.2 Scenario 2: Algorithm Comparison Under Identical Load
All four rate limiting algorithms were evaluated under identical conditions (100 RPS constant arrival rate for 5 seconds across 200 distributed keys):

| Algorithm | Redis Mechanism | Client Latency p50 | Client Latency p95 | Limiter p50 | Limiter p95 | Max Limiter |
|---|---|---|---|---|---|---|
| **Fixed Window** | `MULTI(INCR, PEXPIRE)` | 2.46 ms | 3.27 ms | **0.62 ms** | **0.88 ms** | 32.73 ms |
| **Sliding Window Counter** | `MULTI(INCR, PEXPIRE, GET)` | 2.47 ms | 3.07 ms | **0.61 ms** | **0.86 ms** | 14.32 ms |
| **Token Bucket** | `WATCH` + `HMGET` + `MULTI/EXEC` | 2.66 ms | 3.13 ms | **0.96 ms** | **1.17 ms** | 4.13 ms |
| **Sliding Window Log** | `WATCH` + `ZREMRANGE` + `MULTI/EXEC` | 2.66 ms | 3.17 ms | **0.95 ms** | **1.19 ms** | 6.67 ms |

#### Architectural Analysis:
1. **Pipelined Multi vs Optimistic Transactions:**
   - **Fixed Window** and **Sliding Window Counter** perform a single atomic Redis round-trip (`MULTI/EXEC`), resulting in **0.61–0.62 ms** median evaluation time.
   - **Token Bucket** and **Sliding Window Log** require optimistic transaction isolation (`WATCH` -> read state -> local math -> `MULTI/EXEC`). The extra network round-trip for state read adds ~0.34 ms, yielding **0.95–0.96 ms** median evaluation time.
2. **Predictable Tail Latency:**
   - Even with optimistic locking, Token Bucket and Sliding Window Log maintained p95 limiter durations under **1.2 ms**, proving viable for low-latency API gateways.

---

### 2.3 Scenario 3: Stepped Arrival Rates & Saturation Curve
Evaluated response latency as traffic scaled across 50 RPS, 150 RPS, and 300 RPS:

| Target RPS | Total Reqs | Actual Throughput | Client p50 | Client p95 | Client p99 | Limiter p50 | Limiter p95 |
|---|---|---|---|---|---|---|---|
| **50 RPS** | 250 | 50.0 RPS | 2.90 ms | 3.88 ms | 7.32 ms | 0.73 ms | 1.00 ms |
| **150 RPS** | 750 | 150.0 RPS | 2.46 ms | 8.00 ms | 73.30 ms | 0.53 ms | 1.50 ms |
| **300 RPS** | 1500 | 299.9 RPS | 1.98 ms | 4.79 ms | 16.09 ms | 0.44 ms | 1.09 ms |

#### Saturation Observations:
- System sustained **299.9 RPS** without dropped requests (0 errors across 1,500 requests).
- Internal limiter duration stayed sub-millisecond (**0.44 ms p50** at 300 RPS), demonstrating that the bottleneck in scaling rate limiters is HTTP connection pooling and network round-trips rather than Redis computation.

---

### 2.4 Scenario 4: Hot-Key Contention vs Distributed Key Spread
A primary challenge in distributed rate limiting is **key contention**: when thousands of requests target the exact same client key concurrently.

We compared 150 RPS load targeting a **Single Hot-Key** (`hot-client`) versus **200 Spread Keys** on the Token Bucket algorithm (Capacity: 10, Refill: 2.0 tokens/sec):

| Scenario | Target Rate | Key Distribution | Allowed (200) | Blocked (429) | Limiter p50 | Limiter p95 |
|---|---|---|---|---|---|
| **Single Hot-Key** | 150 RPS | 1 key (`hot-client`) | **19** | **731** | **0.84 ms** | **1.40 ms** |
| **Distributed Spread** | 150 RPS | 200 random keys | **750** | **0** | **0.87 ms** | **1.20 ms** |

#### Strict Enforcement Verification:
1. **Exact Refill Proof:**
   - In the **Single Hot-Key** test, initial bucket capacity was 10 tokens. Over the 5.0 second duration, the bucket refilled at 2.0 tokens/sec ($\approx 9\text{--}10$ tokens).
   - Expected allowed requests: $\approx 19\text{--}20$.
   - **Actual allowed requests:** Exactly **19**. The remaining **731 requests were rejected with HTTP 429**.
   - No token leaks occurred under high concurrency.
2. **Fast-Path Rejection:**
   - Denied requests on Token Bucket do not commit state changes to Redis (lazy refill check detects empty bucket and returns false). This keeps limiter p50 at **0.84 ms** even while rejecting 97.5% of traffic.

---

## 3. Algorithmic Trade-Off Summary

| Algorithm | Memory per Key | Time Complexity | Burst Safety | Contention Sensitivity | Recommended Use Case |
|---|---|---|---|---|---|
| **Fixed Window** | $\mathcal{O}(1)$ (~64 bytes) | $\mathcal{O}(1)$ | ❌ Vulnerable to $2\times$ boundary bursts | Low | Internal batch APIs where bursts are acceptable |
| **Sliding Window Log** | $\mathcal{O}(N)$ ($N$ = request count) | $\mathcal{O}(\log N + M)$ | ✅ Strict enforcement | High under concurrent hot keys | Sensitive auth/login routes, financial transactions |
| **Sliding Window Counter** | $\mathcal{O}(1)$ (~128 bytes) | $\mathcal{O}(1)$ | ✅ Smooths boundary bursts ($\approx 99\%$ accuracy) | Low | High-throughput public endpoints, CDNs (Cloudflare style) |
| **Token Bucket** | $\mathcal{O}(1)$ (~128 bytes) | $\mathcal{O}(1)$ | ✅ Allows controlled burst up to capacity $C$ | Medium (bounded retry on write conflict) | Developer APIs (Stripe/AWS standard), background workers |

---

## 4. Reproducing the Benchmarks

To reproduce these benchmarks locally:

```bash
# 1. Start Redis and backend API
docker compose up -d redis
cd backend && .venv/bin/uvicorn app.main:app --port 8000 &

# 2. Run the benchmark suite
./benchmarks/run.sh http://localhost:8000
```

Raw metrics will be generated in `benchmarks/results/<date>-<sha>/summary.json`.
