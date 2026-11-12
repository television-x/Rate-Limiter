import React from "react";
import { DependencyHealthResponse } from "../types";

interface HealthBadgeProps {
  health: DependencyHealthResponse | null;
  isLoading: boolean;
  error: Error | null;
  isDisconnected: boolean;
}

export const HealthBadge: React.FC<HealthBadgeProps> = ({
  health,
  isLoading,
  error,
  isDisconnected,
}) => {
  if (isDisconnected) {
    return (
      <div className="badge badge-red" data-testid="health-disconnected">
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "currentColor" }} />
        Disconnected (Cluster Unreachable)
      </div>
    );
  }

  if (isLoading && !health) {
    return (
      <div className="badge badge-yellow" data-testid="health-loading">
        Loading Health...
      </div>
    );
  }

  if (error || !health) {
    return (
      <div className="badge badge-red" data-testid="health-error">
        Health Unavailable
      </div>
    );
  }

  const status = health.status;
  const badgeClass =
    status === "healthy" ? "badge-green" : status === "degraded" ? "badge-yellow" : "badge-red";

  const breakerState = health.circuit_breaker.state;
  const latency = health.redis.latency_ms !== null ? `${health.redis.latency_ms} ms` : "n/a";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div className={`badge ${badgeClass}`} data-testid="health-status">
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "currentColor" }} />
        Cluster {status.toUpperCase()}
      </div>
      <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
        Redis: {health.redis.connected ? "Connected" : "Offline"} ({latency})
      </span>
      <span style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
        Breaker: <code style={{ color: breakerState === "closed" ? "var(--accent-green)" : "var(--accent-red)" }}>{breakerState}</code>
      </span>
    </div>
  );
};
