# ADR 0007: Polling vs Server-Sent Events vs WebSockets for Frontend Dashboard

## Status

Accepted (Phase 11)

## Context

The React dashboard (Phase 11) provides live visibility into rate limiter metrics, policy configurations, traffic patterns, and dependency health. To render live counters, latency charts, and status changes, the frontend requires periodic updates from the backend API.

Three potential real-time synchronization patterns were evaluated:
1. **Periodic HTTP Polling (1 second intervals)** with `AbortController` and backoff.
2. **Server-Sent Events (SSE)** (`EventSource` over long-lived HTTP connection).
3. **WebSockets (WS)** (full-duplex TCP socket).

Key architectural and operational constraints:
- The dashboard is deployed behind an Nginx reverse proxy / load balancer.
- Data flow is essentially unidirectional (backend metrics -> dashboard display). User-initiated actions (traffic generation, policy updates, cache resets) are standard idempotent HTTP mutations (`PUT`, `POST`).
- Minimal operational complexity and memory footprint across API replicas.
- Dashboard users may open multiple browser tabs or background tabs.

## Decision

We adopt **1-second HTTP Polling** via a custom React hook (`usePolling`), incorporating:
1. **`AbortController` request cancellation:** Aborts in-flight fetches if a new cycle begins or the component unmounts, preventing race conditions and memory leaks.
2. **Exponential backoff on failure:** When poll requests fail (e.g. backend temporarily unreachable), polling frequency reduces (1s -> 2s -> 4s -> 8s -> max 10s) to prevent hammering recovering services.
3. **Disconnected state detection:** After 3 consecutive poll failures, the UI transitions to an explicit `disconnected` warning banner.

### Rationale Against Server-Sent Events (SSE)
- **Nginx proxy buffering:** In streaming SSE through Nginx, `proxy_buffering off;` and specialized headers (`X-Accel-Buffering: no`) are required; misconfigurations cause event batching or stall event delivery.
- **Connection limits:** Browsers traditionally limit concurrent HTTP/1.1 connections per host (often 6). Long-lived SSE connections can exhaust connection pools for normal API queries in multi-tab scenarios.
- **Reconnect logic:** While SSE provides native auto-reconnect, handling multi-instance load balancers with replica rollouts adds reconnect thrashing for no tangible gain over 1-second metric resolution.

### Rationale Against WebSockets
- **Overkill for unidirectional metrics:** WebSockets provide full-duplex communication, which is unnecessary since client commands are standard REST requests.
- **Stateful load balancing overhead:** WebSockets require persistent TCP connections, complicating Nginx round-robin balancing, increasing socket memory on backend instances, and requiring heartbeat/ping-pong infrastructure.

## Consequences

### Positive
- **Simplicity:** Zero additional backend dependencies or protocols; works with standard REST endpoints (`GET /api/v1/admin/stats`, `GET /health/dependencies`).
- **Nginx compatibility:** Works seamlessly with standard stateless round-robin load balancing without sticky sessions, proxy buffering quirks, or protocol upgrades.
- **Clean error states:** Exponential backoff and poll tracking provide straightforward loading, empty, error, and disconnected states in the UI.

### Negative / Trade-offs
- **Update latency:** Changes take up to 1 second to appear on the dashboard. For human-scale observability of rate limits and traffic spikes, 1 second granularity is completely sufficient.
