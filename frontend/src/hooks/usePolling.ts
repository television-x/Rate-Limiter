import { useEffect, useRef, useState, useCallback } from "react";

export interface PollingState<T> {
  data: T | null;
  isLoading: boolean;
  error: Error | null;
  isDisconnected: boolean;
  consecutiveFailures: number;
  refresh: () => Promise<void>;
}

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number = 1000,
  enabled: boolean = true,
  maxFailuresBeforeDisconnected: number = 3,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<Error | null>(null);
  const [consecutiveFailures, setConsecutiveFailures] = useState<number>(0);

  const abortControllerRef = useRef<AbortController | null>(null);
  const timeoutIdRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef<boolean>(true);
  const failureCountRef = useRef<number>(0);

  const executePoll = useCallback(async () => {
    if (!enabled) return;

    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const result = await fetcher(controller.signal);
      if (!isMountedRef.current) return;

      setData(result);
      setError(null);
      failureCountRef.current = 0;
      setConsecutiveFailures(0);
      setIsLoading(false);

      // Schedule next poll at normal interval
      if (enabled) {
        timeoutIdRef.current = setTimeout(executePoll, intervalMs);
      }
    } catch (err) {
      if (!isMountedRef.current || controller.signal.aborted) return;

      failureCountRef.current += 1;
      const currentFailures = failureCountRef.current;
      setConsecutiveFailures(currentFailures);
      setError(err instanceof Error ? err : new Error(String(err)));
      setIsLoading(false);

      // Exponential backoff up to 10 seconds
      const backoffDelay = Math.min(intervalMs * Math.pow(1.5, currentFailures), 10000);
      if (enabled) {
        timeoutIdRef.current = setTimeout(executePoll, backoffDelay);
      }
    }
  }, [fetcher, intervalMs, enabled]);

  useEffect(() => {
    isMountedRef.current = true;
    failureCountRef.current = 0;
    setIsLoading(true);

    executePoll();

    return () => {
      isMountedRef.current = false;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      if (timeoutIdRef.current) {
        clearTimeout(timeoutIdRef.current);
      }
    };
  }, [executePoll]);

  return {
    data,
    isLoading,
    error,
    isDisconnected: consecutiveFailures >= maxFailuresBeforeDisconnected,
    consecutiveFailures,
    refresh: executePoll,
  };
}
