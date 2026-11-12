import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { PolicyPanel } from "../components/PolicyPanel";
import { Policy } from "../types";
import { ApiClient } from "../api/client";

const mockPolicies: Policy[] = [
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
  {
    id: "token_bucket",
    algorithm: "token_bucket",
    capacity: 20,
    refill_per_sec: 5.0,
    cost_default: 1,
    on_failure: "open",
    on_contention: "reject",
    scope: ["ip"],
  },
];

describe("PolicyPanel", () => {
  const mockClient = new ApiClient();

  it("renders empty state when policies array is empty", () => {
    render(
      <PolicyPanel
        policies={[]}
        selectedPolicyId=""
        onSelectPolicy={vi.fn()}
        adminToken=""
        onAdminTokenChange={vi.fn()}
        apiClient={mockClient}
        onPolicyUpdated={vi.fn()}
      />,
    );
    expect(screen.getByTestId("policy-panel-empty")).toBeInTheDocument();
    expect(screen.getByText(/no policies loaded/i)).toBeInTheDocument();
  });

  it("renders policy details accurately", () => {
    render(
      <PolicyPanel
        policies={mockPolicies}
        selectedPolicyId="default"
        onSelectPolicy={vi.fn()}
        adminToken=""
        onAdminTokenChange={vi.fn()}
        apiClient={mockClient}
        onPolicyUpdated={vi.fn()}
      />,
    );
    expect(screen.getByTestId("policy-panel")).toBeInTheDocument();
    expect(screen.getAllByText(/fixed_window/i).length).toBeGreaterThan(0);
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText("60s")).toBeInTheDocument();
  });

  it("opens edit form when 'Edit Limits' is clicked", () => {
    render(
      <PolicyPanel
        policies={mockPolicies}
        selectedPolicyId="default"
        onSelectPolicy={vi.fn()}
        adminToken="secret"
        onAdminTokenChange={vi.fn()}
        apiClient={mockClient}
        onPolicyUpdated={vi.fn()}
      />,
    );
    const editBtn = screen.getByRole("button", { name: /edit limits/i });
    fireEvent.click(editBtn);

    expect(screen.getByPlaceholderText(/limit \(1-10000\)/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /save limit/i })).toBeInTheDocument();
  });
});
