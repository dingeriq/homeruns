import { X } from "lucide-react";
import { dismissBanner, useDemoMode } from "@/lib/api/demo-mode";

export function DemoBanner() {
  const { isDemoMode, bannerDismissed } = useDemoMode();
  if (!isDemoMode || bannerDismissed) return null;
  return (
    <div
      className="flex items-center justify-between gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-700 dark:text-amber-300"
      role="status"
    >
      <span>Backend unavailable. Running in demo mode.</span>
      <button
        onClick={dismissBanner}
        aria-label="Dismiss"
        className="rounded-md p-1 hover:bg-amber-500/20"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
