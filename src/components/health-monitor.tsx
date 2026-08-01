import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { checkHealth } from "@/lib/api/health";
import { disableDemoMode, enableDemoMode, isDemoMode } from "@/lib/api/demo-mode";

/**
 * Single source of truth for demo mode:
 *  - GET /health succeeds  -> live mode (backend/PostgreSQL data only)
 *  - GET /health fails     -> demo mode (mock fixtures)
 * Polls every 30s in both directions and refetches queries on recovery.
 */
export function HealthMonitor() {
  const queryClient = useQueryClient();

  useEffect(() => {
    let cancelled = false;

    const probe = async () => {
      const ok = await checkHealth();
      if (cancelled) return;
      console.info(`[api] GET /health → ${ok ? "healthy (PostgreSQL live)" : "unreachable"}`);
      if (!ok) {
        enableDemoMode("GET /health failed");
        return;
      }
      if (isDemoMode()) {
        disableDemoMode();
        void queryClient.invalidateQueries();
      }
    };

    void probe();
    const id = window.setInterval(() => void probe(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [queryClient]);

  return null;
}
