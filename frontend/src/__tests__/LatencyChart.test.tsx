import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { LatencyChart } from "../components/LatencyChart";
import { RequestMetric } from "../types";

describe("LatencyChart", () => {
  it("renders empty state when metrics array is empty", () => {
    render(<LatencyChart metrics={[]} />);
    expect(screen.getByTestId("latency-chart-empty")).toBeInTheDocument();
    expect(screen.getByText(/no latency samples yet/i)).toBeInTheDocument();
  });

  it("renders client latency and server limiter timing", () => {
    const mockMetrics: RequestMetric[] = [
      {
        id: "1",
        timestamp: Date.now(),
        policyId: "default",
        algorithm: "fixed_window",
        status: 200,
        servedBy: "api-1",
        clientLatencyMs: 12.5,
        serverLimiterDurationMs: 1.2,
        remaining: 9,
        degraded: false,
      },
    ];

    render(<LatencyChart metrics={mockMetrics} />);
    expect(screen.getByTestId("latency-chart")).toBeInTheDocument();
    expect(screen.getByText("12.5 ms")).toBeInTheDocument();
    expect(screen.getByText("1.2 ms")).toBeInTheDocument();
  });
});

