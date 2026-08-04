// Central API configuration. All API-related env reads live here.
// Switching environments: set VITE_API_BASE_URL in .env, or override at runtime
// from the UI (stored in localStorage) — useful when the hosted app needs to
// point at a deployed/tunnelled backend without a rebuild.

const STORAGE_KEY = "dingeriq.apiBaseUrl";

function normalize(url: string): string {
  return url.trim().replace(/\/$/, "");
}

export const DEFAULT_API_BASE_URL: string =
  normalize((import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "") ||
  "http://localhost:8000";

/** Base URL for all API calls: runtime override (client) → env → localhost. */
export function getApiBaseUrl(): string {
  if (typeof window !== "undefined") {
    try {
      const override = window.localStorage.getItem(STORAGE_KEY);
      if (override) return normalize(override);
    } catch {
      // ignore storage errors
    }
  }
  return DEFAULT_API_BASE_URL;
}

export function setApiBaseUrl(url: string) {
  if (typeof window === "undefined") return;
  try {
    const value = normalize(url);
    if (value) window.localStorage.setItem(STORAGE_KEY, value);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore storage errors
  }
}

/**
 * True when the page is served over HTTPS but the API base URL is plain HTTP.
 * Browsers block these requests (mixed content), which is why a hosted app can
 * never reach a `http://localhost:8000` backend.
 */
export function isMixedContentBlocked(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.location.protocol === "https:" &&
    getApiBaseUrl().startsWith("http://")
  );
}

/** @deprecated prefer getApiBaseUrl() so runtime overrides are respected. */
export const API_BASE_URL: string = DEFAULT_API_BASE_URL;

export const API_KEY: string | undefined = import.meta.env.VITE_API_KEY as
  | string
  | undefined;

export type EnvLabel = "Local" | "Development" | "Production";

/**
 * Human-readable environment label derived from hostname.
 * SSR returns a stable "Local" default so hydration stays deterministic;
 * the real label is resolved on the client after mount.
 */
export function getEnvLabel(): EnvLabel {
  if (typeof window === "undefined") return "Local";
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1" || host.endsWith(".local")) {
    return "Local";
  }
  if (
    host.includes("lovableproject.com") ||
    host.includes("preview") ||
    host.includes("-dev")
  ) {
    return "Development";
  }
  return "Production";
}
