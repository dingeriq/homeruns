import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { ErrorPanel, LoadingPanel } from "@/components/query-states";
import { modelPerformanceQuery } from "@/lib/api/queries";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, Legend } from "recharts";

export const Route = createFileRoute("/performance")({
  head: () => ({ meta: [{ title: "Model Performance — Dinger IQ" }] }),
  component: Performance,
});

function Performance() {
  const q = useQuery(modelPerformanceQuery());

  if (q.isLoading) {
    return (
      <DashboardShell title="Model Performance" subtitle="Walk-forward validation">
        <LoadingPanel />
      </DashboardShell>
    );
  }
  if (q.isError || !q.data) {
    return (
      <DashboardShell title="Model Performance" subtitle="—">
        <ErrorPanel error={q.error ?? new Error("No data")} onRetry={() => q.refetch()} />
      </DashboardShell>
    );
  }

  const { models, calibration, rolling_auc: rollingAuc, headline } = q.data;

  return (
    <DashboardShell title="Model Performance" subtitle="Walk-forward validation · 2025 season">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="ROC-AUC" value={headline.roc_auc.toFixed(3)} delta={`${headline.champion} champion`} tone="positive" />
        <StatCard label="PR-AUC" value={headline.pr_auc.toFixed(3)} />
        <StatCard label="Log Loss" value={headline.log_loss.toFixed(3)} />
        <StatCard label="Brier" value={headline.brier.toFixed(3)} />
        <StatCard label="ECE" value={headline.ece.toFixed(3)} tone="positive" />
      </div>

      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <div className="p-4 border-b border-border">
          <h2 className="text-sm font-semibold">Model comparison</h2>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="text-left px-4 py-2">Model</th>
              <th className="text-right px-4 py-2">Log Loss</th>
              <th className="text-right px-4 py-2">Brier</th>
              <th className="text-right px-4 py-2">ROC-AUC</th>
              <th className="text-right px-4 py-2">PR-AUC</th>
              <th className="text-right px-4 py-2">ECE</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m, i) => (
              <tr key={m.name} className={`border-t border-border ${m.name === headline.champion ? "bg-emerald-500/5" : ""}`}>
                <td className="px-4 py-2 font-medium">{m.name}{m.name === headline.champion && <span className="ml-2 text-xs text-emerald-500">★ champion</span>}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.log_loss.toFixed(3)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.brier.toFixed(3)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.roc_auc.toFixed(3)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.pr_auc.toFixed(3)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.ece.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3">Reliability diagram</h2>
          <ResponsiveContainer width="100%" height={280}>
            <ScatterChart>
              <CartesianGrid stroke="var(--color-border)" />
              <XAxis type="number" dataKey="predicted" domain={[0, 1]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} stroke="var(--color-muted-foreground)" fontSize={12} />
              <YAxis type="number" dataKey="observed" domain={[0, 1]} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} stroke="var(--color-muted-foreground)" fontSize={12} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="var(--color-muted-foreground)" strokeDasharray="4 4" />
              <Scatter data={calibration} fill="oklch(0.65 0.2 25)" />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3">Rolling 4-week AUC</h2>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={rollingAuc}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis dataKey="week" stroke="var(--color-muted-foreground)" fontSize={11} />
              <YAxis domain={[0.65, 0.78]} stroke="var(--color-muted-foreground)" fontSize={12} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="auc" stroke="oklch(0.65 0.2 25)" strokeWidth={2} dot={false} name="ROC-AUC" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </DashboardShell>
  );
}
