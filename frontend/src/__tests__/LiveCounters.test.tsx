import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { LiveCounters } from "../components/LiveCounters";
import { PolicyStatsResponse } from "../types";

describe("LiveCounters", () => {
  it("renders loading state", () => {
    render(<LiveCounters stats={null} isLoading={true} error={null} isDisconnected={false} />);
    expect(screen.getByTestId("live-counters-loading")).toBeInTheDocument();
  });

  it("renders error state", () => {
    render(<LiveCounters stats={null} isLoading={false} error={new Error("Failed")} isDisconnected={false} />);
    expect(screen.getByTestId("live-counters-error")).toBeInTheDocument();
  });

  it("renders disconnected state", () => {
    render(<LiveCounters stats={null} isLoading={false} error={null} isDisconnected={true} />);
    expect(screen.getByTestId("live-counters-disconnected")).toBeInTheDocument();
  });

  it("renders counts, rejection rate, sparkline, and top clients", () => {
    const mockStats: PolicyStatsResponse = {
      policy_id: "default",
      window_s: 60,
      total_allowed: 80,
      total_rejected: 20,
      series: [
        { timestamp_s: 100, allowed: 10, rejected: 0 },
        { timestamp_s: 101, allowed: 5, rejected: 5 },
      ],
      top_clients: [
        { client_hash: "a1b2c3d4e5f6", allowed: 50, rejected: 10, total: 60 },
      ],
    };

    render(<LiveCounters stats={mockStats} isLoading={false} error={null} isDisconnected={false} />);
    expect(screen.getByTestId("live-counters")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    expect(screen.getByText("20%")).toBeInTheDocument(); // 20 / 100 = 20%
    expect(screen.getByTestId("sparkline-container")).toBeInTheDocument();
    expect(screen.getByText("a1b2c3d4e5f6")).toBeInTheDocument();
  });
});
