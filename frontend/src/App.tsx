import React, { useState, useCallback, useEffect } from "react";
import { Policy, RequestMetric } from "./types";
import { defaultApiClient } from "./api/client";
import { usePolling } from "./hooks/usePolling";
import { HealthBadge } from "./components/HealthBadge";
import { PolicyPanel } from "./components/PolicyPanel";
import { TrafficGenerator } from "./components/TrafficGenerator";
import { LiveCounters } from "./components/LiveCounters";
import { LatencyChart } from "./components/LatencyChart";
import { AlgorithmCompare } from "./components/AlgorithmCompare";

export const App: React.FC = () => {
  const [adminToken, setAdminToken] = useState<string>("dev-admin-token");
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [selectedPolicyId, setSelectedPolicyId] = useState<string>("default");
  const [requestMetrics, setRequestMetrics] = useState<RequestMetric[]>([]);

  // 1. Fetch policies on initial mount
  const loadPolicies = useCallback(async () => {
    try {
      const list = await defaultApiClient.fetchPolicies();
      setPolicies(list);
      if (list.length > 0 && !list.some((p) => p.id === selectedPolicyId)) {
        setSelectedPolicyId(list[0].id);
      }
    } catch (err) {
      console.error("Failed to load policies:", err);
    }
  }, [selectedPolicyId]);

  useEffect(() => {
    loadPolicies();
  }, [loadPolicies]);

  // 2. Poll cluster dependency health every 2 seconds
  const healthFetcher = useCallback(
    (signal: AbortSignal) => defaultApiClient.fetchDependencyHealth(adminToken, signal),
    [adminToken],
  );
  const healthState = usePolling(healthFetcher, 2000);

  // 3. Poll live policy stats every 1 second (per AGENTS.md §11)
  const statsFetcher = useCallback(
    (signal: AbortSignal) => defaultApiClient.fetchStats(selectedPolicyId, 60, adminToken, signal),
    [selectedPolicyId, adminToken],
  );
  const statsState = usePolling(statsFetcher, 1000);

  const handleNewMetrics = (newMetrics: RequestMetric[]) => {
    setRequestMetrics((prev) => [...newMetrics, ...prev].slice(0, 50));
    statsState.refresh();
  };

  const handleClearMetrics = () => {
    setRequestMetrics([]);
  };

  return (
    <div className="dashboard-container">
      {/* Header */}
      <header className="dashboard-header">
        <div className="dashboard-title">
          <h1>Distributed Rate Limiter</h1>
          <span className="badge badge-blue">Multi-Instance Demo</span>
        </div>
        <HealthBadge
          health={healthState.data}
          isLoading={healthState.isLoading}
          error={healthState.error}
          isDisconnected={healthState.isDisconnected}
        />
      </header>

      {/* Grid Layout */}
      <main className="dashboard-grid">
        {/* Left Column: Configuration & Generator */}
        <div style={{ gridColumn: "span 6", display: "flex", flexDirection: "column", gap: 20 }}>
          <PolicyPanel
            policies={policies}
            selectedPolicyId={selectedPolicyId}
            onSelectPolicy={setSelectedPolicyId}
            adminToken={adminToken}
            onAdminTokenChange={setAdminToken}
            apiClient={defaultApiClient}
            onPolicyUpdated={() => {
              loadPolicies();
              statsState.refresh();
            }}
          />

          <TrafficGenerator
            apiClient={defaultApiClient}
            metrics={requestMetrics}
            onNewMetrics={handleNewMetrics}
            onClearMetrics={handleClearMetrics}
          />
        </div>

        {/* Right Column: Live Counters & Latency Analysis */}
        <div style={{ gridColumn: "span 6", display: "flex", flexDirection: "column", gap: 20 }}>
          <LiveCounters
            stats={statsState.data}
            isLoading={statsState.isLoading}
            error={statsState.error}
            isDisconnected={statsState.isDisconnected}
          />

          <LatencyChart metrics={requestMetrics} />
        </div>

        {/* Full-Width Row: Side-by-Side Algorithm Comparison */}
        <div style={{ gridColumn: "span 12" }}>
          <AlgorithmCompare apiClient={defaultApiClient} />
        </div>
      </main>
    </div>
  );
};

export default App;
