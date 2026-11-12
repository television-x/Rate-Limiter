import React from "react";
import { PolicyStatsResponse } from "../types";

interface LiveCountersProps {
  stats: PolicyStatsResponse | null;
  isLoading: boolean;
  error: Error | null;
  isDisconnected: boolean;
}

export const LiveCounters: React.FC<LiveCountersProps> = ({
  stats,
  isLoading,
  error,
  isDisconnected,
}) => {
  if (isDisconnected) {
    return (
      <div className="card" data-testid="live-counters-disconnected">
        <h2 className="card-title">Live Throughput</h2>
        <p style={{ color: "var(--accent-red)", fontSize: "0.875rem" }}>
          Live metrics disconnected from cluster. Retrying with exponential backoff...
        </p>
      </div>
    );
  }

  if (isLoading && !stats) {
    return (
      <div className="card" data-testid="live-counters-loading">
        <h2 className="card-title">Live Throughput</h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem" }}>Loading rolling statistics...</p>
      </div>
    );
  }

  if (error || !stats) {
    return (
      <div className="card" data-testid="live-counters-error">
        <h2 className="card-title">Live Throughput</h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem" }}>
          Stats unavailable for this policy (requires admin token or traffic).
        </p>
      </div>
    );
  }

  const total = stats.total_allowed + stats.total_rejected;
  const rejectionRate = total > 0 ? Math.round((stats.total_rejected / total) * 100) : 0;

  // Find max value in series for sparkline normalization
  const maxInSeries = Math.max(
    1,
    ...stats.series.map((pt) => pt.allowed + pt.rejected),
  );

  return (
    <div className="card" data-testid="live-counters">
      <div className="card-title">
        <span>Live Window Metrics (Last {stats.window_s}s)</span>
        <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          Total: {total.toLocaleString()}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 16 }}>
        <div className="stat-metric">
          <span className="stat-label">Allowed (200)</span>
          <span className="stat-value" style={{ color: "var(--accent-green)" }}>
            {stats.total_allowed.toLocaleString()}
          </span>
        </div>
        <div className="stat-metric">
          <span className="stat-label">Rejected (429)</span>
          <span className="stat-value" style={{ color: "var(--accent-red)" }}>
            {stats.total_rejected.toLocaleString()}
          </span>
        </div>
        <div className="stat-metric">
          <span className="stat-label">Rejection Rate</span>
          <span className="stat-value" style={{ color: rejectionRate > 0 ? "var(--accent-yellow)" : "var(--text-primary)" }}>
            {rejectionRate}%
          </span>
        </div>
      </div>

      {/* 60-Second Sparkline Visualization */}
      <div>
        <span className="stat-label" style={{ marginBottom: 6, display: "block" }}>
          Throughput Activity (1 Bar = 1 Second)
        </span>
        <div
          style={{
            height: 48,
            display: "flex",
            alignItems: "flex-end",
            background: "var(--bg-primary)",
            padding: "4px 8px",
            borderRadius: 6,
            border: "1px solid var(--border-color)",
          }}
          data-testid="sparkline-container"
        >
          {stats.series.map((pt, i) => {
            const sum = pt.allowed + pt.rejected;
            const heightPercent = sum > 0 ? Math.max(15, Math.round((sum / maxInSeries) * 100)) : 4;
            const barColor =
              pt.rejected > 0
                ? "var(--accent-red)"
                : pt.allowed > 0
                ? "var(--accent-green)"
                : "rgba(48, 54, 61, 0.4)";

            return (
              <div
                key={i}
                className="sparkline-bar"
                style={{
                  height: `${heightPercent}%`,
                  backgroundColor: barColor,
                  flex: 1,
                }}
                title={`Second ${pt.timestamp_s}: ${pt.allowed} allowed, ${pt.rejected} rejected`}
              />
            );
          })}
        </div>
      </div>

      {/* Top Clients Table */}
      {stats.top_clients.length > 0 && (
        <div className="table-container">
          <span className="stat-label" style={{ marginBottom: 6, display: "block" }}>
            Top Active Clients (Truncated Hashes)
          </span>
          <table>
            <thead>
              <tr>
                <th>Client Hash</th>
                <th>Allowed</th>
                <th>Rejected</th>
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {stats.top_clients.map((c) => (
                <tr key={c.client_hash}>
                  <td><code>{c.client_hash}</code></td>
                  <td style={{ color: "var(--accent-green)" }}>{c.allowed}</td>
                  <td style={{ color: c.rejected > 0 ? "var(--accent-red)" : "inherit" }}>
                    {c.rejected}
                  </td>
                  <td><strong>{c.total}</strong></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
