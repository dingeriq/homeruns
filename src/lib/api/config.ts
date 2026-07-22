// Central API configuration. All API-related env reads live here.
// Switching environments: set VITE_API_BASE_URL in .env; nothing else changes.

export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ||
  "http://localhost:8000";

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
