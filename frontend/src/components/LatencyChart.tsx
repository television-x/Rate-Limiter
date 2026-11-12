import React from "react";
import { RequestMetric } from "../types";

interface LatencyChartProps {
  metrics: RequestMetric[];
}

export const LatencyChart: React.FC<LatencyChartProps> = ({ metrics }) => {
  const recent = metrics.slice(0, 30);

  if (recent.length === 0) {
    return (
      <div className="card" data-testid="latency-chart-empty">
        <h2 className="card-title">Latency Analysis (Client vs Limiter Duration)</h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.875rem" }}>
          No latency samples yet. Send requests from the Traffic Generator to visualize latency.
        </p>
      </div>
    );
  }

  const clientLatencies = recent.map((m) => m.clientLatencyMs);
  const serverDurations = recent
    .map((m) => m.serverLimiterDurationMs)
    .filter((v): v is number => v !== null);

  const avgClient = Math.round((clientLatencies.reduce((a, b) => a + b, 0) / clientLatencies.length) * 10) / 10;
  const avgServer =
    serverDurations.length > 0
      ? Math.round((serverDurations.reduce((a, b) => a + b, 0) / serverDurations.length) * 100) / 100
      : null;

  const maxClient = Math.max(...clientLatencies, 1);

  return (
    <div className="card" data-testid="latency-chart">
      <div className="card-title">
        <span>Latency Analysis (Client vs Server-Timing)</span>
        <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
          {recent.length} recent samples
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <div className="stat-metric">
          <span className="stat-label">Avg Client Latency (Roundtrip)</span>
          <span className="stat-value" style={{ color: "var(--accent-blue)" }}>
            {avgClient} ms
          </span>
        </div>
        <div className="stat-metric">
          <span className="stat-label">Avg Limiter Duration (Redis Overhead)</span>
          <span className="stat-value" style={{ color: "var(--accent-purple)" }}>
            {avgServer !== null ? `${avgServer} ms` : "n/a"}
          </span>
        </div>
      </div>

      {/* Latency Comparison Bars */}
      <div>
        <span className="stat-label" style={{ marginBottom: 8, display: "block" }}>
          Recent Request Latency Breakdown (Blue = Client Total, Purple = Limiter Dur)
        </span>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 180, overflowY: "auto" }}>
          {recent.slice(0, 10).map((m) => {
            const clientWidth = Math.min(100, Math.max(5, (m.clientLatencyMs / maxClient) * 100));
            const serverWidth =
              m.serverLimiterDurationMs !== null
                ? Math.min(100, Math.max(3, (m.serverLimiterDurationMs / maxClient) * 100))
                : 0;

            return (
              <div key={m.id} style={{ fontSize: "0.75rem", display: "flex", flexDirection: "column", gap: 2 }}>
                <div style={{ display: "flex", justifyContent: "space-between", color: "var(--text-secondary)" }}>
                  <span>{m.status} | {m.servedBy}</span>
                  <span>
                    Client: <strong>{m.clientLatencyMs}ms</strong> | Limiter:{" "}
                    <strong>{m.serverLimiterDurationMs !== null ? `${m.serverLimiterDurationMs}ms` : "—"}</strong>
                  </span>
                </div>
                <div style={{ display: "flex", height: 8, background: "var(--bg-primary)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${serverWidth}%`, background: "var(--accent-purple)" }} />
                  <div style={{ width: `${clientWidth - serverWidth}%`, background: "var(--accent-blue)" }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

