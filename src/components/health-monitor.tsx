import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { checkHealth } from "@/lib/api/health";
import { disableDemoMode, enableDemoMode, useDemoMode } from "@/lib/api/demo-mode";

/**
 * Client-only mount:
 *  - Runs one `/health` check on mount. If it fails, enables demo mode.
 *  - While demo mode is active, polls `/health` every 30s. On first success:
 *    disables demo mode, invalidates all React Query caches so active queries
 *    refetch from the live backend.
 */
export function HealthMonitor() {
  const queryClient = useQueryClient();
  const { isDemoMode } = useDemoMode();

  // Startup probe (runs once).
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const ok = await checkHealth();
      if (cancelled) return;
      if (!ok) {
        enableDemoMode("startup /health failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Recovery poll while in demo mode.
  useEffect(() => {
    if (!isDemoMode) return;
    let cancelled = false;
    const id = window.setInterval(async () => {
      const ok = await checkHealth();
      if (cancelled || !ok) return;
      disableDemoMode();
      // Invalidate everything so active queries refetch from the live API.
      void queryClient.invalidateQueries();
    }, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [isDemoMode, queryClient]);

  return null;
}
