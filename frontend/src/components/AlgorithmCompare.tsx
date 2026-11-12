import React, { useState } from "react";
import { AlgorithmComparisonSummary, RateLimitAlgorithm } from "../types";
import { ApiClient } from "../api/client";

interface AlgorithmCompareProps {
  apiClient: ApiClient;
}

const ALGORITHMS: RateLimitAlgorithm[] = [
  "fixed_window",
  "token_bucket",
  "sliding_window_log",
  "sliding_window_counter",
];

const ALGORITHM_DESCRIPTIONS: Record<RateLimitAlgorithm, string> = {
  fixed_window: "Simple counter reset at fixed time boundaries. Allows up to 2x burst across window boundaries.",
  token_bucket: "Refills tokens continuously at a smooth rate. Allows bursts up to capacity, then throttles.",
  sliding_window_log: "Tracks exact timestamps in a sorted set. Mathematically strict sliding window enforcement.",
  sliding_window_counter: "Memory-efficient weighted estimate combining current and previous window counts.",
};

export const AlgorithmCompare: React.FC<AlgorithmCompareProps> = ({ apiClient }) => {
  const [burstSize, setBurstSize] = useState<number>(12);
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [summaries, setSummaries] = useState<AlgorithmComparisonSummary[] | null>(null);

  const runComparison = async () => {
    setIsRunning(true);
    const results: AlgorithmComparisonSummary[] = [];

    // Unique user ID per comparison run to avoid carry-over limits
    const testUserId = `compare-${Date.now()}`;

    try {
      for (const algo of ALGORITHMS) {
        const path = `/api/v1/demo/limited/${algo}`;
        const promises = Array.from({ length: burstSize }).map(() =>
          apiClient.sendDemoRequest(path, { userId: testUserId }),
        );
        const batchResults = await Promise.all(promises);

        const allowed = batchResults.filter((r) => r.metric.status === 200).length;
        const rejected = batchResults.filter((r) => r.metric.status === 429).length;

        const totalClientLat = batchResults.reduce((acc, r) => acc + r.metric.clientLatencyMs, 0);
        const serverTimings = batchResults
          .map((r) => r.metric.serverLimiterDurationMs)
          .filter((v): v is number => v !== null);

        const avgClientLat = Math.round((totalClientLat / burstSize) * 10) / 10;
        const avgServerLat =
          serverTimings.length > 0
            ? Math.round((serverTimings.reduce((a, b) => a + b, 0) / serverTimings.length) * 100) / 100
            : 0;

        results.push({
          algorithm: algo,
          totalSent: burstSize,
          allowed,
          rejected,
          avgClientLatencyMs: avgClientLat,
          avgServerLimiterMs: avgServerLat,
        });
      }
      setSummaries(results);
    } catch (err) {
      console.error("Comparison test error:", err);
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <div className="card" data-testid="algorithm-compare">
      <div className="card-title">
        <span>Side-by-Side Algorithm Benchmark Comparison</span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>Burst Size:</label>
          <input
            type="number"
            min="1"
            max="50"
            className="input-field"
            style={{ width: 70 }}
            value={burstSize}
            onChange={(e) => setBurstSize(parseInt(e.target.value, 10) || 1)}
          />
          <button
            type="button"
            className="btn btn-primary"
            onClick={runComparison}
            disabled={isRunning}
          >
            {isRunning ? "Testing Algorithms..." : "Run Multi-Algorithm Burst"}
          </button>
        </div>
      </div>

      <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
        Sends the exact same synchronized burst of {burstSize} requests against all four algorithm endpoints
        (each with limit/capacity = 10) using a fresh isolated identity.
      </p>

      {summaries && (
        <div className="table-container" style={{ marginTop: 12 }}>
          <table>
            <thead>
              <tr>
                <th>Algorithm</th>
                <th>Sent</th>
                <th>Allowed</th>
                <th>Rejected</th>
                <th>Avg Client Latency</th>
                <th>Avg Server Limiter</th>
                <th>Behavioral Characteristic</th>
              </tr>
            </thead>
            <tbody>
              {summaries.map((s) => (
                <tr key={s.algorithm}>
                  <td><strong><code>{s.algorithm}</code></strong></td>
                  <td>{s.totalSent}</td>
                  <td style={{ color: "var(--accent-green)", fontWeight: 700 }}>{s.allowed}</td>
                  <td style={{ color: s.rejected > 0 ? "var(--accent-red)" : "inherit", fontWeight: 700 }}>
                    {s.rejected}
                  </td>
                  <td>{s.avgClientLatencyMs} ms</td>
                  <td style={{ color: "var(--accent-purple)" }}>{s.avgServerLimiterMs} ms</td>
                  <td style={{ fontSize: "0.75rem", color: "var(--text-secondary)", maxWidth: 300 }}>
                    {ALGORITHM_DESCRIPTIONS[s.algorithm]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

