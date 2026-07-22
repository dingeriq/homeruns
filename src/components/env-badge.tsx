import { useEffect, useState } from "react";
import { getEnvLabel, type EnvLabel } from "@/lib/api/config";
import { useDemoMode } from "@/lib/api/demo-mode";
import { cn } from "@/lib/utils";

const tone: Record<EnvLabel | "Demo Mode", string> = {
  Local: "bg-slate-500/15 text-slate-600 dark:text-slate-300 border-slate-500/30",
  Development: "bg-blue-500/15 text-blue-600 dark:text-blue-300 border-blue-500/30",
  Production: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300 border-emerald-500/30",
  "Demo Mode": "bg-amber-500/15 text-amber-600 dark:text-amber-300 border-amber-500/30",
};

export function EnvBadge() {
  const { isDemoMode } = useDemoMode();
  // Resolve env label client-side after mount to avoid hydration mismatch.
  const [label, setLabel] = useState<EnvLabel>("Local");
  useEffect(() => {
    setLabel(getEnvLabel());
  }, []);

  const shown: EnvLabel | "Demo Mode" = isDemoMode ? "Demo Mode" : label;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
        tone[shown],
      )}
    >
      {shown}
    </span>
  );
}
