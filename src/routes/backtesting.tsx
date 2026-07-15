import { createFileRoute } from "@tanstack/react-router";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { backtest } from "@/lib/mock-data";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export const Route = createFileRoute("/backtesting")({
  head: () => ({ meta: [{ title: "Backtesting — Dinger IQ" }] }),
  component: Backtesting,
});

function Backtesting() {
  const cum = backtest.months.reduce<{ label: string; cumRoi: number }[]>((acc, m, i) => {
    const prev = i === 0 ? 0 : acc[i - 1].cumRoi;
    acc.push({ label: m.label, cumRoi: prev + m.roi });
    return acc;
  }, []);
  return (
    <DashboardShell title="Backtesting" subtitle="Top-25 daily picks · flat $100 unit · 2024-25">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="Total picks" value={backtest.summary.totalBets.toLocaleString()} />
        <StatCard label="Hit rate" value={`${(backtest.summary.hitRate * 100).toFixed(1)}%`} />
        <StatCard label="ROI" value={`+${backtest.summary.roi}%`} tone="positive" />
        <StatCard label="Sharpe" value={backtest.summary.sharpe.toFixed(2)} tone="positive" />
        <StatCard label="Max drawdown" value={`${backtest.summary.maxDrawdown}%`} tone="negative" />
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
            <BarChart data={backtest.months}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="var(--color-muted-foreground)" fontSize={11} />
              <YAxis stroke="var(--color-muted-foreground)" fontSize={12} tickFormatter={(v) => `${v}%`} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
              <Bar dataKey="roi" radius={[4, 4, 0, 0]}>
                {backtest.months.map((m, i) => (
                  <Cell key={i} fill={m.roi >= 0 ? "oklch(0.7 0.2 145)" : "oklch(0.6 0.22 25)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3">Monthly hit rate</h2>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={backtest.months}>
              <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" />
              <XAxis dataKey="label" stroke="var(--color-muted-foreground)" fontSize={11} />
              <YAxis stroke="var(--color-muted-foreground)" fontSize={12} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} formatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
              <Line type="monotone" dataKey="hitRate" stroke="oklch(0.65 0.2 25)" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </DashboardShell>
  );
}
