import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { dismissBanner, useDemoMode, disableDemoMode } from "@/lib/api/demo-mode";
import {
  getApiBaseUrl,
  setApiBaseUrl,
  isMixedContentBlocked,
} from "@/lib/api/config";
import { checkHealth } from "@/lib/api/health";

export function DemoBanner() {
  const { isDemoMode, bannerDismissed } = useDemoMode();
  const queryClient = useQueryClient();
  const [url, setUrl] = useState("");
  const [mixed, setMixed] = useState(false);
  const [status, setStatus] = useState<null | "checking" | "failed">(null);

  useEffect(() => {
    setUrl(getApiBaseUrl());
    setMixed(isMixedContentBlocked());
  }, [isDemoMode]);

  if (!isDemoMode || bannerDismissed) return null;

  const connect = async () => {
    setStatus("checking");
    setApiBaseUrl(url);
    setMixed(isMixedContentBlocked());
    const ok = await checkHealth();
    if (ok) {
      disableDemoMode();
      void queryClient.invalidateQueries();
      setStatus(null);
    } else {
      setStatus("failed");
    }
  };

  return (
    <div
      className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-700 dark:text-amber-300"
      role="status"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <p className="font-medium">Backend unavailable. Running in demo mode.</p>
          <p className="text-xs opacity-90">
            {mixed
              ? `This page is served over HTTPS, so the browser blocks requests to ${getApiBaseUrl()}. A hosted dashboard cannot reach a backend on your own machine — deploy the API (or expose it over HTTPS with a tunnel) and paste the public URL below.`
              : `No response from ${getApiBaseUrl()}/health. Paste a reachable backend URL below.`}
          </p>
        </div>
        <button
          onClick={dismissBanner}
          aria-label="Dismiss"
          className="rounded-md p-1 hover:bg-amber-500/20"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://your-api.example.com"
          aria-label="Backend API base URL"
          className="min-w-[220px] flex-1 rounded-md border border-amber-500/40 bg-background px-2 py-1 text-xs text-foreground"
        />
        <button
          onClick={() => void connect()}
          disabled={status === "checking"}
          className="rounded-md border border-amber-500/50 bg-amber-500/20 px-3 py-1 text-xs font-medium hover:bg-amber-500/30 disabled:opacity-60"
        >
          {status === "checking" ? "Checking…" : "Connect"}
        </button>
        {status === "failed" && (
          <span className="text-xs">Still unreachable — check the URL and CORS.</span>
        )}
      </div>
    </div>
  );
}
