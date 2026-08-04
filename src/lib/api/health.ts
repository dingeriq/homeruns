import { getApiBaseUrl } from "./config";

/** Ping the backend `/health` endpoint with a 3s timeout. */
export async function checkHealth(): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetch(`${getApiBaseUrl()}/health`, {
      method: "GET",
      signal: ctrl.signal,
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}
