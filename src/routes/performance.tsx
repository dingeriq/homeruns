import { createFileRoute } from "@tanstack/react-router";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { modelPerformance } from "@/lib/mock-data";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, Legend } from "recharts";

export const Route = createFileRoute("/performance")({
  head: () => ({ meta: [{ title: "Model Performance — Dinger IQ" }] }),
  component: Performance,
});

function Performance() {
  const { models, calibration, rollingAuc } = modelPerformance;
  return (
    <DashboardShell title="Model Performance" subtitle="Walk-forward validation · 2025 season">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="ROC-AUC" value="0.742" delta="XGBoost champion" tone="positive" />
        <StatCard label="PR-AUC" value="0.187" />
        <StatCard label="Log Loss" value="0.284" />
        <StatCard label="Brier" value="0.081" />
        <StatCard label="ECE" value="0.014" tone="positive" />
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
              <tr key={m.name} className={`border-t border-border ${i === 0 ? "bg-emerald-500/5" : ""}`}>
                <td className="px-4 py-2 font-medium">{m.name}{i === 0 && <span className="ml-2 text-xs text-emerald-500">★ champion</span>}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.logLoss.toFixed(3)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.brier.toFixed(3)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.rocAuc.toFixed(3)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{m.prAuc.toFixed(3)}</td>
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
