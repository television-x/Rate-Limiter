/**
 * TypeScript domain models for the rate limiter dashboard.
 * Mirrored directly from the OpenAPI specification (docs/openapi.json).
 */

export type RateLimitAlgorithm =
  | "fixed_window"
  | "token_bucket"
  | "sliding_window_log"
  | "sliding_window_counter";

export interface Policy {
  id: string;
  algorithm: RateLimitAlgorithm;
  limit?: number | null;
  window_ms?: number | null;
  capacity?: number | null;
  refill_per_sec?: number | null;
  cost_default: number;
  on_failure: "open" | "closed" | "local_fallback";
  on_contention: "reject" | "allow";
  scope: string[];
}

export interface StatsPoint {
  timestamp_s: number;
  allowed: number;
  rejected: number;
}

export interface TopClientInfo {
  client_hash: string;
  allowed: number;
  rejected: number;
  total: number;
}

export interface PolicyStatsResponse {
  policy_id: string;
  window_s: number;
  total_allowed: number;
  total_rejected: number;
  series: StatsPoint[];
  top_clients: TopClientInfo[];
}

export interface RedisDependencyInfo {
  connected: boolean;
  latency_ms: number | null;
}

export interface CircuitBreakerInfo {
  state: "closed" | "open" | "half_open";
  consecutive_failures: number;
  failure_threshold: number;
}

export interface DependencyHealthResponse {
  status: "healthy" | "degraded" | "unhealthy";
  redis: RedisDependencyInfo;
  circuit_breaker: CircuitBreakerInfo;
}

export interface RateLimitHeaders {
  limit?: number;
  remaining?: number;
  reset?: number;
  retryAfter?: number;
  servedBy?: string;
  degraded?: boolean;
  accuracy?: string;
  serverTimingDurationMs?: number;
}

export interface RequestMetric {
  id: string;
  timestamp: number;
  policyId: string;
  algorithm: string;
  status: number;
  servedBy: string;
  clientLatencyMs: number;
  serverLimiterDurationMs: number | null;
  remaining: number | null;
  degraded: boolean;
}

export interface AlgorithmComparisonSummary {
  algorithm: RateLimitAlgorithm;
  totalSent: number;
  allowed: number;
  rejected: number;
  avgClientLatencyMs: number;
  avgServerLimiterMs: number;
}

