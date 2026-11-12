import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { TrafficGenerator } from "../components/TrafficGenerator";
import { ApiClient } from "../api/client";

describe("TrafficGenerator", () => {
  it("renders target path options, user ID input, and empty log message", () => {
    const mockClient = new ApiClient();
    render(
      <TrafficGenerator
        apiClient={mockClient}
        metrics={[]}
        onNewMetrics={vi.fn()}
        onClearMetrics={vi.fn()}
      />,
    );
    expect(screen.getByTestId("traffic-generator")).toBeInTheDocument();
    expect(screen.getByLabelText(/target api path/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("client-demo-1")).toBeInTheDocument();
    expect(screen.getByText(/no requests sent yet/i)).toBeInTheDocument();
  });

  it("calls sendDemoRequest when 'Send 1 Request' is clicked", async () => {
    const mockClient = new ApiClient();
    const sendSpy = vi.spyOn(mockClient, "sendDemoRequest").mockResolvedValue({
      response: new Response("{}", { status: 200 }),
      metric: {
        id: "m-1",
        timestamp: Date.now(),
        policyId: "default",
        algorithm: "fixed_window",
        status: 200,
        servedBy: "api-1",
        clientLatencyMs: 10,
        serverLimiterDurationMs: 0.8,
        remaining: 9,
        degraded: false,
      },
    });

    const onNewMetrics = vi.fn();
    render(
      <TrafficGenerator
        apiClient={mockClient}
        metrics={[]}
        onNewMetrics={onNewMetrics}
        onClearMetrics={vi.fn()}
      />,
    );

    const sendBtn = screen.getByRole("button", { name: /send 1 request/i });
    fireEvent.click(sendBtn);

    await vi.waitFor(() => {
      expect(sendSpy).toHaveBeenCalledTimes(1);
      expect(onNewMetrics).toHaveBeenCalledWith(
        expect.arrayContaining([expect.objectContaining({ status: 200, servedBy: "api-1" })]),
      );
    });
  });
});
