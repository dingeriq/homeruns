// Thin fetch wrapper over the FastAPI service. Reads config from ./config.ts.
// It never toggles demo mode: only a failing GET /health does that
// (see ./health.ts, ./fallback.ts and components/health-monitor.tsx).

import { getApiBaseUrl, API_KEY } from "./config";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (API_KEY) headers.set("X-API-Key", API_KEY);

  let res: Response;
  try {
    res = await fetch(`${getApiBaseUrl()}${path}`, { ...init, headers });
  } catch (err) {
    throw new ApiError(0, err instanceof Error ? err.message : "Network error");
  }

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // ignore
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

