import React, { useState } from "react";
import { RequestMetric } from "../types";
import { ApiClient } from "../api/client";

interface TrafficGeneratorProps {
  apiClient: ApiClient;
  metrics: RequestMetric[];
  onNewMetrics: (newMetrics: RequestMetric[]) => void;
  onClearMetrics: () => void;
}

export const TrafficGenerator: React.FC<TrafficGeneratorProps> = ({
  apiClient,
  metrics,
  onNewMetrics,
  onClearMetrics,
}) => {
  const [selectedPath, setSelectedPath] = useState<string>("/api/v1/demo/ping");
  const [userId, setUserId] = useState<string>("client-demo-1");
  const [burstCount, setBurstCount] = useState<number>(5);
  const [isSending, setIsSending] = useState<boolean>(false);

  const handleSendBurst = async (count: number) => {
    setIsSending(true);
    const collected: RequestMetric[] = [];

    // Send requests with bounded concurrency (up to 5 concurrent)
    const concurrency = Math.min(count, 5);
    const batches = Math.ceil(count / concurrency);

    try {
      for (let b = 0; b < batches; b++) {
        const batchSize = Math.min(concurrency, count - b * concurrency);
        const promises = Array.from({ length: batchSize }).map(() =>
          apiClient.sendDemoRequest(selectedPath, { userId }),
        );
        const results = await Promise.all(promises);
        results.forEach((r) => collected.push(r.metric));
      }
      onNewMetrics(collected);
    } catch (err) {
      console.error("Burst generation error:", err);
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="card" data-testid="traffic-generator">
      <div className="card-title">
        <span>Traffic Generator</span>
        <button
          type="button"
          className="btn btn-secondary"
          style={{ fontSize: "0.75rem", padding: "4px 8px" }}
          onClick={onClearMetrics}
        >
          Clear Log
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12 }}>
        <div>
          <label style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>Target Path</label>
          <select
            className="input-field"
            value={selectedPath}
            onChange={(e) => setSelectedPath(e.target.value)}
            aria-label="Target API path"
          >
            <option value="/api/v1/demo/ping">/api/v1/demo/ping (Default)</option>
            <option value="/api/v1/demo/strict">/api/v1/demo/strict (Strict)</option>
            <option value="/api/v1/demo/limited/fixed_window">/demo/limited/fixed_window</option>
            <option value="/api/v1/demo/limited/token_bucket">/demo/limited/token_bucket</option>
            <option value="/api/v1/demo/limited/sliding_window_log">/demo/limited/sliding_window_log</option>
            <option value="/api/v1/demo/limited/sliding_window_counter">/demo/limited/sliding_window_counter</option>
          </select>
        </div>

        <div>
          <label style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>Client User ID (Demo Mode)</label>
          <input
            type="text"
            className="input-field"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            placeholder="User identity"
          />
        </div>

        <div>
          <label style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>Burst Size</label>
          <input
            type="number"
            min="1"
            max="100"
            className="input-field"
            value={burstCount}
            onChange={(e) => setBurstCount(parseInt(e.target.value, 10) || 1)}
          />
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => handleSendBurst(1)}
          disabled={isSending}
        >
          Send 1 Request
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => handleSendBurst(burstCount)}
          disabled={isSending}
        >
          {isSending ? "Dispatching..." : `Send Burst of ${burstCount}`}
        </button>
      </div>

      {/* Real-time Request Log */}
      <div className="table-container" style={{ maxHeight: 240, overflowY: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Served By</th>
              <th>Client Latency</th>
              <th>Server-Timing</th>
              <th>Remaining</th>
              <th>Flags</th>
            </tr>
          </thead>
          <tbody>
            {metrics.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", color: "var(--text-secondary)", padding: 24 }}>
                  No requests sent yet. Click 'Send Burst' above to generate traffic.
                </td>
              </tr>
            ) : (
              metrics.slice(0, 20).map((m) => {
                const statusColor =
                  m.status === 200
                    ? "var(--accent-green)"
                    : m.status === 429
                    ? "var(--accent-red)"
                    : "var(--accent-yellow)";
                return (
                  <tr key={m.id}>
                    <td style={{ color: statusColor, fontWeight: 700 }}>{m.status}</td>
                    <td><code>{m.servedBy}</code></td>
                    <td>{m.clientLatencyMs} ms</td>
                    <td>{m.serverLimiterDurationMs !== null ? `${m.serverLimiterDurationMs} ms` : "n/a"}</td>
                    <td>{m.remaining !== null ? m.remaining : "—"}</td>
                    <td>
                      {m.degraded && (
                        <span className="badge badge-yellow" style={{ fontSize: "0.65rem", padding: "2px 6px" }}>
                          Degraded
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

