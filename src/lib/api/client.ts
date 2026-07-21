// Client for the Phase 10 FastAPI service.
// Configure via Vite env:
//   VITE_API_BASE_URL   e.g. https://api.dingeriq.app
//   VITE_API_KEY        optional X-API-Key
const BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const KEY = import.meta.env.VITE_API_KEY as string | undefined;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  if (!BASE) {
    throw new ApiError(0, "VITE_API_BASE_URL is not configured");
  }
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (KEY) headers.set("X-API-Key", KEY);

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
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
