import React, { useState } from "react";
import { Policy } from "../types";
import { ApiClient } from "../api/client";

interface PolicyPanelProps {
  policies: Policy[];
  selectedPolicyId: string;
  onSelectPolicy: (id: string) => void;
  adminToken: string;
  onAdminTokenChange: (token: string) => void;
  apiClient: ApiClient;
  onPolicyUpdated: () => void;
}

export const PolicyPanel: React.FC<PolicyPanelProps> = ({
  policies,
  selectedPolicyId,
  onSelectPolicy,
  adminToken,
  onAdminTokenChange,
  apiClient,
  onPolicyUpdated,
}) => {
  const policy = policies.find((p) => p.id === selectedPolicyId) || policies[0];

  const [isEditing, setIsEditing] = useState(false);
  const [newLimit, setNewLimit] = useState<string>("");
  const [newCapacity, setNewCapacity] = useState<string>("");
  const [newRefill, setNewRefill] = useState<string>("");
  const [resetIdentity, setResetIdentity] = useState<string>("demo-client");
  const [statusMsg, setStatusMsg] = useState<{ text: string; error: boolean } | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!policies || policies.length === 0) {
    return (
      <div className="card" data-testid="policy-panel-empty">
        <h2 className="card-title">Policy Configuration</h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem" }}>
          No policies loaded. Ensure backend is running.
        </p>
      </div>
    );
  }

  const startEdit = () => {
    if (policy) {
      setNewLimit(policy.limit ? String(policy.limit) : "");
      setNewCapacity(policy.capacity ? String(policy.capacity) : "");
      setNewRefill(policy.refill_per_sec ? String(policy.refill_per_sec) : "");
      setIsEditing(true);
      setStatusMsg(null);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!adminToken) {
      setStatusMsg({ text: "Admin token required to modify policy limits", error: true });
      return;
    }

    setIsSubmitting(true);
    setStatusMsg(null);

    try {
      const updatePayload: Partial<Policy> = {};
      if (policy.algorithm === "token_bucket") {
        if (newCapacity) updatePayload.capacity = parseInt(newCapacity, 10);
        if (newRefill) updatePayload.refill_per_sec = parseFloat(newRefill);
      } else {
        if (newLimit) updatePayload.limit = parseInt(newLimit, 10);
      }

      await apiClient.updatePolicy(policy.id, updatePayload, adminToken);
      setStatusMsg({ text: `Policy '${policy.id}' updated successfully!`, error: false });
      setIsEditing(false);
      onPolicyUpdated();
    } catch (err) {
      setStatusMsg({ text: err instanceof Error ? err.message : String(err), error: true });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleResetCounter = async () => {
    if (!adminToken) {
      setStatusMsg({ text: "Admin token required to reset client counter", error: true });
      return;
    }
    setIsSubmitting(true);
    try {
      await apiClient.resetRateLimit(policy.id, resetIdentity, "user", adminToken);
      setStatusMsg({ text: `Reset counter for '${resetIdentity}' on policy '${policy.id}'`, error: false });
    } catch (err) {
      setStatusMsg({ text: err instanceof Error ? err.message : String(err), error: true });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="card" data-testid="policy-panel">
      <div className="card-title">
        <span>Policy: <strong style={{ color: "var(--accent-blue)" }}>{policy?.id}</strong></span>
        <select
          className="input-field"
          style={{ width: "auto" }}
          value={selectedPolicyId}
          onChange={(e) => onSelectPolicy(e.target.value)}
          aria-label="Select rate limiting policy"
        >
          {policies.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id} ({p.algorithm})
            </option>
          ))}
        </select>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 12 }}>
        <div className="stat-metric">
          <span className="stat-label">Algorithm</span>
          <span style={{ fontSize: "0.95rem", fontWeight: 600 }}><code>{policy.algorithm}</code></span>
        </div>
        {policy.algorithm === "token_bucket" ? (
          <>
            <div className="stat-metric">
              <span className="stat-label">Capacity</span>
              <span className="stat-value" style={{ fontSize: "1.3rem" }}>{policy.capacity}</span>
            </div>
            <div className="stat-metric">
              <span className="stat-label">Refill Rate</span>
              <span className="stat-value" style={{ fontSize: "1.3rem" }}>{policy.refill_per_sec}/s</span>
            </div>
          </>
        ) : (
          <>
            <div className="stat-metric">
              <span className="stat-label">Limit</span>
              <span className="stat-value" style={{ fontSize: "1.3rem" }}>{policy.limit}</span>
            </div>
            <div className="stat-metric">
              <span className="stat-label">Window</span>
              <span className="stat-value" style={{ fontSize: "1.3rem" }}>
                {policy.window_ms ? `${policy.window_ms / 1000}s` : "n/a"}
              </span>
            </div>
          </>
        )}
        <div className="stat-metric">
          <span className="stat-label">Failure Mode</span>
          <span style={{ fontSize: "0.95rem" }}><code>{policy.on_failure}</code></span>
        </div>
      </div>

      {statusMsg && (
        <div
          className={`badge ${statusMsg.error ? "badge-red" : "badge-green"}`}
          style={{ width: "100%", justifyContent: "center", padding: "8px 12px" }}
        >
          {statusMsg.text}
        </div>
      )}

      {isEditing ? (
        <form onSubmit={handleSave} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {policy.algorithm === "token_bucket" ? (
            <div style={{ display: "flex", gap: 8 }}>
              <input
                type="number"
                min="1"
                max="10000"
                className="input-field"
                placeholder="Capacity (1-10000)"
                value={newCapacity}
                onChange={(e) => setNewCapacity(e.target.value)}
              />
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="1000"
                className="input-field"
                placeholder="Refill/s (0.1-1000)"
                value={newRefill}
                onChange={(e) => setNewRefill(e.target.value)}
              />
            </div>
          ) : (
            <input
              type="number"
              min="1"
              max="10000"
              className="input-field"
              placeholder="Limit (1-10000)"
              value={newLimit}
              onChange={(e) => setNewLimit(e.target.value)}
            />
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="btn btn-primary" disabled={isSubmitting}>
              {isSubmitting ? "Updating..." : "Save Limit"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setIsEditing(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={startEdit}
          >
            Edit Limits
          </button>

          <input
            type="password"
            className="input-field"
            style={{ maxWidth: 180 }}
            placeholder="Admin Token"
            value={adminToken}
            onChange={(e) => onAdminTokenChange(e.target.value)}
          />

          <div style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
            <input
              type="text"
              className="input-field"
              style={{ width: 120 }}
              value={resetIdentity}
              onChange={(e) => setResetIdentity(e.target.value)}
              placeholder="User ID"
            />
            <button
              type="button"
              className="btn btn-danger"
              onClick={handleResetCounter}
              disabled={isSubmitting}
            >
              Reset Quota
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

