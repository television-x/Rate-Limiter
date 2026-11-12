# Operations Guide: Distributed Rate Limiter

This guide describes how to operate, monitor, verify, and troubleshoot the distributed rate limiter cluster.

---

## 1. System Architecture

The distributed rate limiter deployment consists of five coordinated services across two isolated Docker networks:

```
                  Client Traffic (Port 8080)
                              │
                              ▼
                   ┌─────────────────────┐
                   │  Nginx Load Balancer│
                   │       (rl-lb)       │
                   └──────────┬──────────┘
                              │
                 Round-Robin (proxy_next_upstream off)
                              │
           ┌──────────────────┼──────────────────┐
           ▼                  ▼                  ▼
     ┌───────────┐      ┌───────────┐      ┌───────────┐
     │   api1    │      │   api2    │      │   api3    │
     │ (rl-api-1)│      │ (rl-api-2)│      │ (rl-api-3)│
     └─────┬─────┘      └─────┬─────┘      └─────┬─────┘
           │                  │                  │
           └──────────────────┼──────────────────┘
                              │
                              ▼ (backend network only)
                   ┌─────────────────────┐
                   │   Redis 7 (Storage) │
                   │      (rl-redis)     │
                   └─────────────────────┘
```

### Network Topology & Security Boundaries
- **`edge` network:** Public-facing network containing the Nginx Load Balancer, the three API replicas, and Prometheus.
- **`backend` network:** Internal isolated network containing Redis and the API replicas.
- **Security Rule:** Redis has **no interface on the edge network**. Direct external access is prevented. Public `/metrics` requests through the load balancer are blocked (return 404).

---

## 2. Common Operational Commands

### Starting the Cluster
Build and start all containers with automatic health checks:
```bash
docker compose up -d --build
# or
make up
```

### Verifying Service Status
Inspect the healthcheck status of all running services:
```bash
docker compose ps
```
Expected output:
```
NAME            IMAGE                  COMMAND                  SERVICE      CREATED         STATUS                   PORTS
rl-api-1        rate-limiter-api1      "uvicorn app.main:ap…"   api1         1 minute ago    Up 1 minute (healthy)    8000/tcp
rl-api-2        rate-limiter-api2      "uvicorn app.main:ap…"   api2         1 minute ago    Up 1 minute (healthy)    8000/tcp
rl-api-3        rate-limiter-api3      "uvicorn app.main:ap…"   api3         1 minute ago    Up 1 minute (healthy)    8000/tcp
rl-lb           nginx:alpine           "/docker-entrypoint.…"   lb           1 minute ago    Up 1 minute              0.0.0.0:8080->80/tcp
rl-prometheus   prom/prometheus:v2.54.1 "/bin/prometheus --c…"   prometheus   1 minute ago    Up 1 minute              127.0.0.1:9090->9090/tcp
rl-redis        redis:7-alpine         "docker-entrypoint.s…"   redis        1 minute ago    Up 1 minute (healthy)    127.0.0.1:6379:6379->6379/tcp
```

### Stopping the Cluster
Gracefully stop all containers (`stop_grace_period: 15s` allows in-flight transactions to settle):
```bash
docker compose down
# or with volume cleanup
make down
```

---

## 3. Health Checks & Diagnostic Endpoints

| Endpoint | Auth | Purpose | Success Code |
|---|---|---|---|
| `GET http://localhost:8080/healthz` | None | Process liveness probe | `200 OK` |
| `GET http://localhost:8080/readyz` | None | Storage readiness probe (cached ~1s) | `200 OK` (or `503 Service Unavailable`) |
| `GET http://localhost:8080/health/dependencies` | Admin Bearer | Latency to Redis & Circuit Breaker status | `200 OK` |
| `GET http://localhost:9090` | Prometheus UI | Prometheus time-series exploration & metrics | `200 OK` |

### Probing Dependency Health
```bash
curl -s -H "Authorization: Bearer dev-admin-token" http://localhost:8080/health/dependencies | jq .
```
Response:
```json
{
  "status": "healthy",
  "redis": {
    "connected": true,
    "latency_ms": 1.25
  },
  "circuit_breaker": {
    "state": "closed",
    "consecutive_failures": 0,
    "failure_threshold": 5
  }
}
```

---

## 4. Demonstrating Distributed Rate Limiting

### The Shared-State Proof
1. Send 15 requests to a strict policy (configured for limit = 10):
   ```bash
   for i in {1..15}; do
     curl -s -i http://localhost:8080/api/v1/demo/limited/fixed_window | grep -E "(HTTP|X-Served-By|X-RateLimit)"
   done
   ```
2. **Observations:**
   - The `X-Served-By` response header alternates across `api-1`, `api-2`, and `api-3`.
   - Exactly **10 requests** return `200 OK`.
   - The remaining **5 requests** return `429 Too Many Requests`.
   - This proves that quota is tracked centrally in Redis across all distinct replica processes.

### The Negative Control
If instances were configured with in-memory storage (`STORAGE=memory`), each server would maintain an isolated local counter. Sending 15 round-robin requests would allow roughly 5 requests on each of the 3 instances, allowing **all 15 requests** to pass and effectively tripling the intended rate limit.

---

## 5. Troubleshooting & Resilience

### Simulating a Redis Outage
Pause the Redis container to test circuit breaking and failure policies:
```bash
docker compose pause redis
```
1. Requests hitting policies configured with `on_failure: open` will continue to return `200 OK` with degraded headers:
   ```http
   HTTP/1.1 200 OK
   X-RateLimit-Status: degraded
   ```
2. The Circuit Breaker automatically trips to `open` after 5 consecutive failures, shielding Redis from request storms.
3. `/readyz` returns `503 Service Unavailable`.

### Recovery Without App Restart
Resume Redis:
```bash
docker compose unpause redis
```
1. On the next request after the cooldown window (5 seconds), the circuit breaker enters `half_open` probe state.
2. The probe succeeds, transitioning the breaker back to `closed`.
3. Normal distributed rate limiting resumes immediately without restarting any API process.

