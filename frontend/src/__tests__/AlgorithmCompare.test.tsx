import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AlgorithmCompare } from "../components/AlgorithmCompare";
import { ApiClient } from "../api/client";

describe("AlgorithmCompare", () => {
  it("renders comparison title and launch button", () => {
    const mockClient = new ApiClient();
    render(<AlgorithmCompare apiClient={mockClient} />);
    expect(screen.getByTestId("algorithm-compare")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run multi-algorithm burst/i })).toBeInTheDocument();
  });

  it("executes comparison and renders algorithm comparison table", async () => {
    const mockClient = new ApiClient();
    vi.spyOn(mockClient, "sendDemoRequest").mockResolvedValue({
      response: new Response("{}", { status: 200 }),
      metric: {
        id: "m-c",
        timestamp: Date.now(),
        policyId: "algo",
        algorithm: "fixed_window",
        status: 200,
        servedBy: "api-1",
        clientLatencyMs: 8,
        serverLimiterDurationMs: 0.5,
        remaining: 8,
        degraded: false,
      },
    });

    render(<AlgorithmCompare apiClient={mockClient} />);
    const runBtn = screen.getByRole("button", { name: /run multi-algorithm burst/i });
    fireEvent.click(runBtn);

    await vi.waitFor(() => {
      expect(screen.getByText("token_bucket")).toBeInTheDocument();
      expect(screen.getByText("sliding_window_log")).toBeInTheDocument();
      expect(screen.getByText("sliding_window_counter")).toBeInTheDocument();
    });
  });
});

