import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { HealthBadge } from "../components/HealthBadge";
import { DependencyHealthResponse } from "../types";

describe("HealthBadge", () => {
  it("renders loading state", () => {
    render(<HealthBadge health={null} isLoading={true} error={null} isDisconnected={false} />);
    expect(screen.getByTestId("health-loading")).toBeInTheDocument();
    expect(screen.getByText(/loading health/i)).toBeInTheDocument();
  });

  it("renders error state", () => {
    render(<HealthBadge health={null} isLoading={false} error={new Error("Network Error")} isDisconnected={false} />);
    expect(screen.getByTestId("health-error")).toBeInTheDocument();
    expect(screen.getByText(/health unavailable/i)).toBeInTheDocument();
  });

  it("renders disconnected state when cluster is unreachable", () => {
    render(<HealthBadge health={null} isLoading={false} error={null} isDisconnected={true} />);
    expect(screen.getByTestId("health-disconnected")).toBeInTheDocument();
    expect(screen.getByText(/disconnected/i)).toBeInTheDocument();
  });

  it("renders healthy cluster status with redis latency and breaker state", () => {
    const mockHealth: DependencyHealthResponse = {
      status: "healthy",
      redis: { connected: true, latency_ms: 1.5 },
      circuit_breaker: { state: "closed", consecutive_failures: 0, failure_threshold: 5 },
    };

    render(<HealthBadge health={mockHealth} isLoading={false} error={null} isDisconnected={false} />);
    expect(screen.getByTestId("health-status")).toBeInTheDocument();
    expect(screen.getByText(/cluster healthy/i)).toBeInTheDocument();
    expect(screen.getByText(/redis: connected \(1.5 ms\)/i)).toBeInTheDocument();
    expect(screen.getByText(/closed/i)).toBeInTheDocument();
  });
});

