import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { ErrorPanel, LoadingPanel } from "@/components/query-states";
import { backtestQuery } from "@/lib/api/queries";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const Route = createFileRoute("/backtesting")({
  head: () => ({ meta: [{ title: "Backtesting — Dinger IQ" }] }),
  component: Backtesting,
});

function Backtesting() {
  const q = useQuery(backtestQuery());

  if (q.isLoading) {
    return (
      <DashboardShell title="Backtesting" subtitle="Loading backtest…">
        <LoadingPanel />
      </DashboardShell>
    );
  }
  if (q.isError || !q.data) {
    return (
      <DashboardShell title="Backtesting" subtitle="—">
        <ErrorPanel error={q.error ?? new Error("No data")} onRetry={() => q.refetch()} />
      </DashboardShell>
    );
  }

  const { months, summary } = q.data;
  const cum = months.reduce<{ label: string; cumRoi: number }[]>((acc, m, i) => {
    const prev = i === 0 ? 0 : acc[i - 1].cumRoi;
    acc.push({ label: m.label, cumRoi: prev + m.roi });
    return acc;
  }, []);

  return (
    <DashboardShell title="Backtesting" subtitle="Top-25 daily picks · flat $100 unit">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="Total picks" value={summary.total_bets.toLocaleString()} />
        <StatCard label="Hit rate" value={`${(summary.hit_rate * 100).toFixed(1)}%`} />
        <StatCard label="ROI" value={`${summary.roi >= 0 ? "+" : ""}${summary.roi.toFixed(1)}%`} tone={summary.roi >= 0 ? "positive" : "negative"} />
        <StatCard label="Sharpe" value={summary.sharpe.toFixed(2)} tone="positive" />
        <StatCard label="Max drawdown" value={`${summary.max_drawdown.toFixed(1)}%`} tone="negative" />
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-3">Cumulative ROI</h2>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={cum}>
            <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
            <XAxis dataKey="label" stroke="var(--color-muted-foreground)" fontSize={11} />
            <YAxis stroke="var(--color-muted-foreground)" fontSize={12} tickFormatter={(v) => `${v}%`} />
            <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
            <Line type="monotone" dataKey="cumRoi" stroke="oklch(0.7 0.2 145)" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3">Monthly ROI</h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={months}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="var(--color-muted-foreground)" fontSize={11} />
              <YAxis stroke="var(--color-muted-foreground)" fontSize={12} tickFormatter={(v) => `${v}%`} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
              <Bar dataKey="roi" radius={[4, 4, 0, 0]}>
                {months.map((m, i) => (
                  <Cell key={i} fill={m.roi >= 0 ? "oklch(0.7 0.2 145)" : "oklch(0.6 0.22 25)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3">Monthly hit rate</h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={months}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="var(--color-muted-foreground)" fontSize={11} />
              <YAxis stroke="var(--color-muted-foreground)" fontSize={12} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} formatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
              <Line type="monotone" dataKey="hit_rate" stroke="oklch(0.65 0.2 25)" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </DashboardShell>
  );
}
