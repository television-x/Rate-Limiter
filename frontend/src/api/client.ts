/**
 * API client communicating with backend endpoints.
 * Handles rate-limit headers, Server-Timing parsing, and admin token authorization.
 */

import {
  DependencyHealthResponse,
  Policy,
  PolicyStatsResponse,
  RateLimitHeaders,
  RequestMetric,
} from "../types";

export function parseServerTiming(header: string | null): number | null {
  if (!header) return null;
  // Format: "limiter;dur=1.23"
  const match = /dur=([0-9.]+)/.exec(header);
  return match ? parseFloat(match[1]) : null;
}

export function parseRateLimitHeaders(headers: Headers): RateLimitHeaders {
  const limit = headers.get("X-RateLimit-Limit");
  const remaining = headers.get("X-RateLimit-Remaining");
  const reset = headers.get("X-RateLimit-Reset");
  const retryAfter = headers.get("Retry-After");
  const servedBy = headers.get("X-Served-By") || undefined;
  const status = headers.get("X-RateLimit-Status");
  const accuracy = headers.get("X-RateLimit-Accuracy") || undefined;
  const serverTiming = parseServerTiming(headers.get("Server-Timing"));

  return {
    limit: limit ? parseInt(limit, 10) : undefined,
    remaining: remaining ? parseInt(remaining, 10) : undefined,
    reset: reset ? parseInt(reset, 10) : undefined,
    retryAfter: retryAfter ? parseInt(retryAfter, 10) : undefined,
    servedBy,
    degraded: status === "degraded",
    accuracy,
    serverTimingDurationMs: serverTiming ?? undefined,
  };
}

export class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = "") {
    this.baseUrl = baseUrl;
  }

  async fetchPolicies(signal?: AbortSignal): Promise<Policy[]> {
    const res = await fetch(`${this.baseUrl}/api/v1/policies`, { signal });
    if (!res.ok) {
      throw new Error(`Failed to fetch policies: ${res.statusText}`);
    }
    const data = await res.json();
    return data.policies || [];
  }

  async fetchStats(
    policyId: string,
    windowS: number = 60,
    adminToken?: string,
    signal?: AbortSignal,
  ): Promise<PolicyStatsResponse> {
    const headers: Record<string, string> = {};
    if (adminToken) {
      headers["Authorization"] = `Bearer ${adminToken}`;
    }

    const res = await fetch(
      `${this.baseUrl}/api/v1/admin/stats?policy_id=${encodeURIComponent(policyId)}&window_s=${windowS}`,
      { headers, signal },
    );

    if (!res.ok) {
      throw new Error(`Failed to fetch stats: ${res.status} ${res.statusText}`);
    }
    return res.json();
  }

  async fetchDependencyHealth(
    adminToken?: string,
    signal?: AbortSignal,
  ): Promise<DependencyHealthResponse> {
    const headers: Record<string, string> = {};
    if (adminToken) {
      headers["Authorization"] = `Bearer ${adminToken}`;
    }

    const res = await fetch(`${this.baseUrl}/health/dependencies`, {
      headers,
      signal,
    });

    if (!res.ok) {
      throw new Error(`Failed to fetch health: ${res.status} ${res.statusText}`);
    }
    return res.json();
  }

  async updatePolicy(
    policyId: string,
    update: Partial<Policy>,
    adminToken: string,
  ): Promise<Policy> {
    const res = await fetch(`${this.baseUrl}/api/v1/admin/policies/${encodeURIComponent(policyId)}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${adminToken}`,
      },
      body: JSON.stringify(update),
    });

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.detail || `Failed to update policy: ${res.status}`);
    }
    return res.json();
  }

  async resetRateLimit(
    policyId: string,
    identity: string,
    kind: string = "user",
    adminToken: string = "dev-admin-token",
  ): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/v1/admin/reset`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${adminToken}`,
      },
      body: JSON.stringify({
        policy_id: policyId,
        identity,
        kind,
      }),
    });

    if (!res.ok) {
      throw new Error(`Failed to reset rate limit: ${res.status}`);
    }
  }

  async sendDemoRequest(
    path: string,
    options: {
      userId?: string;
      cost?: number;
      signal?: AbortSignal;
    } = {},
  ): Promise<{ response: Response; metric: RequestMetric }> {
    const headers: Record<string, string> = {};
    if (options.userId) {
      headers["X-User-Id"] = options.userId;
    }
    if (options.cost) {
      headers["X-Cost"] = String(options.cost);
    }

    const start = performance.now();
    const res = await fetch(`${this.baseUrl}${path}`, {
      headers,
      signal: options.signal,
    });
    const clientLatencyMs = performance.now() - start;

    const parsed = parseRateLimitHeaders(res.headers);

    const metric: RequestMetric = {
      id: crypto.randomUUID ? crypto.randomUUID() : String(Math.random()),
      timestamp: Date.now(),
      policyId: path.includes("strict")
        ? "strict"
        : path.split("/").pop() || "default",
      algorithm: path.split("/").pop() || "fixed_window",
      status: res.status,
      servedBy: parsed.servedBy || "unknown",
      clientLatencyMs: Math.round(clientLatencyMs * 10) / 10,
      serverLimiterDurationMs: parsed.serverTimingDurationMs ?? null,
      remaining: parsed.remaining ?? null,
      degraded: parsed.degraded ?? false,
    };

    return { response: res, metric };
  }
}

export const defaultApiClient = new ApiClient();
