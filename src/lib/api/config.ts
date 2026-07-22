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
 * Derive a human-readable environment label from the current hostname and
 * configured API base URL. SSR returns a stable "Local" default to keep
 * hydration deterministic — the real label is resolved on the client.
 */
export function getEnvLabel(): EnvLabel {
  if (typeof window === "undefined") return "Local";
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1" || host.endsWith(".local")) {
    return "Local";
  }
  if (
    host.includes("lovableproject.com") ||
    host.includes("lovable.app") && host.includes("-dev") ||
    host.includes("preview") ||
    /API_BASE_URL/i.test("") // placeholder — see below
  ) {
    // Preview / dev hosts
    if (
      host.includes("lovableproject.com") ||
      host.includes("preview") ||
      host.includes("-dev")
    ) {
      return "Development";
    }
  }
  return "Production";
}
