// Single place that decides live-vs-mock.
// Rule: mock data is served ONLY when the backend health check fails.
// Any other error (404, 500, bad payload) surfaces to the UI as an error.

import { checkHealth } from "./health";
import { enableDemoMode, isDemoMode } from "./demo-mode";
import { logApi } from "./log";

function count(v: unknown): number {
  return Array.isArray(v) ? v.length : v == null ? 0 : 1;
}

export async function withFallback<T>(
  endpoint: string,
  fn: () => Promise<T>,
  fallback: () => T,
): Promise<T> {
  if (isDemoMode()) {
    const healthy = await checkHealth();
    if (!healthy) {
      const data = fallback();
      logApi(endpoint, count(data), "mock", "demo mode — /health unreachable");
      return data;
    }
  }
  try {
    return await fn();
  } catch (err) {
    const healthy = await checkHealth();
    if (healthy) {
      // Backend is up: this is a real error, do not mask it with mock data.
      console.error(`[api] ${endpoint} failed while backend is healthy:`, err);
      throw err;
    }
    enableDemoMode(`${endpoint} failed and /health is unreachable`);
    const data = fallback();
    logApi(endpoint, count(data), "mock", "/health unreachable");
    return data;
  }
}
