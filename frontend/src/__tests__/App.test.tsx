import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import App from "../App";
import { defaultApiClient } from "../api/client";

describe("App", () => {
  it("renders the dashboard layout and main headings", async () => {
    vi.spyOn(defaultApiClient, "fetchPolicies").mockResolvedValue([
      {
        id: "default",
        algorithm: "fixed_window",
        limit: 60,
        window_ms: 60000,
        cost_default: 1,
        on_failure: "open",
        on_contention: "reject",
        scope: ["ip"],
      },
    ]);
    vi.spyOn(defaultApiClient, "fetchDependencyHealth").mockResolvedValue({
      status: "healthy",
      redis: { connected: true, latency_ms: 1.1 },
      circuit_breaker: { state: "closed", consecutive_failures: 0, failure_threshold: 5 },
    });
    vi.spyOn(defaultApiClient, "fetchStats").mockResolvedValue({
      policy_id: "default",
      window_s: 60,
      total_allowed: 10,
      total_rejected: 2,
      series: [],
      top_clients: [],
    });

    render(<App />);

    expect(screen.getByText(/distributed rate limiter/i)).toBeInTheDocument();
    expect(screen.getByText(/multi-instance demo/i)).toBeInTheDocument();
    expect(await screen.findByTestId("policy-panel")).toBeInTheDocument();
    expect(screen.getByTestId("traffic-generator")).toBeInTheDocument();
  });
});
