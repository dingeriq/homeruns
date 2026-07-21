import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DashboardShell } from "@/components/dashboard-shell";
import { ErrorPanel, LoadingPanel } from "@/components/query-states";
import { featureImportanceQuery } from "@/lib/api/queries";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/features")({
  head: () => ({ meta: [{ title: "Feature Importance — Dinger IQ" }] }),
  component: Features,
});

const groupColors: Record<string, string> = {
  Hitter: "oklch(0.65 0.2 25)",
  Pitcher: "oklch(0.65 0.18 260)",
  Ballpark: "oklch(0.7 0.18 145)",
  Weather: "oklch(0.75 0.15 200)",
  Matchup: "oklch(0.7 0.18 60)",
  Vegas: "oklch(0.6 0.22 340)",
  Opportunity: "oklch(0.65 0.15 100)",
};

function Features() {
  const [metric, setMetric] = useState<"gain" | "permutation" | "shap">("shap");
  const q = useQuery(featureImportanceQuery());

  const sorted = [...(q.data ?? [])].sort((a, b) => b[metric] - a[metric]);

  return (
    <DashboardShell title="Feature Importance" subtitle="XGBoost champion · SHAP mean|φ| across 2025 validation">
      <div className="flex flex-wrap gap-2">
        {(["shap", "gain", "permutation"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMetric(m)}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-medium border transition-colors",
              metric === m ? "bg-primary text-primary-foreground border-primary" : "bg-card text-muted-foreground border-border hover:text-foreground",
            )}
          >
            {m === "shap" ? "Mean |SHAP|" : m === "gain" ? "Gain" : "Permutation ΔLogLoss"}
          </button>
        ))}
      </div>

      {q.isLoading && <LoadingPanel />}
      {q.isError && <ErrorPanel error={q.error} onRetry={() => q.refetch()} />}

      {!q.isLoading && !q.isError && (
        <div className="rounded-lg border border-border bg-card p-4">
          <ResponsiveContainer width="100%" height={480}>
            <BarChart data={sorted} layout="vertical" margin={{ left: 140 }}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis type="number" stroke="var(--color-muted-foreground)" fontSize={12} />
              <YAxis type="category" dataKey="feature" stroke="var(--color-muted-foreground)" fontSize={11} width={130} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
              <Bar dataKey={metric} radius={[0, 4, 4, 0]}>
                {sorted.map((f, i) => (
                  <Cell key={i} fill={groupColors[f.group] ?? "oklch(0.6 0.1 260)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-3">Group legend</h2>
        <div className="flex flex-wrap gap-3">
          {Object.entries(groupColors).map(([g, c]) => (
            <div key={g} className="flex items-center gap-2 text-xs">
              <span className="w-3 h-3 rounded-sm" style={{ background: c }} />
              {g}
            </div>
          ))}
        </div>
      </div>
    </DashboardShell>
  );
}
