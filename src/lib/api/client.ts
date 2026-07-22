// Thin fetch wrapper over the FastAPI service. Reads config from ./config.ts.
// On any failure, flips the app into demo mode and rethrows so callers
// (React Query queryFns) can decide whether to substitute mock data.

import { API_BASE_URL, API_KEY } from "./config";
import { enableDemoMode } from "./demo-mode";

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
    res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch (err) {
    enableDemoMode(`network error on ${path}`);
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
    enableDemoMode(`HTTP ${res.status} on ${path}`);
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}
