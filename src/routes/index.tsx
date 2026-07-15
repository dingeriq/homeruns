import { createFileRoute, Link } from "@tanstack/react-router";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { rankings } from "@/lib/mock-data";
import { Bar, BarChart, ResponsiveContainer, XAxis, YAxis, Tooltip, Cell } from "recharts";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Daily HR Rankings — Dinger IQ" },
      { name: "description", content: "Top MLB home run candidates with probability, confidence, and SHAP drivers." },
    ],
  }),
  component: Rankings,
});

function Rankings() {
  const top = rankings.slice(0, 10);
  return (
    <DashboardShell title="Daily Home Run Rankings" subtitle="Top 25 candidates from today's slate">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Slate size" value="15 games" />
        <StatCard label="Hitters scored" value="342" />
        <StatCard label="Top prob" value="28.4%" delta="Judge" tone="positive" />
        <StatCard label="Avg confidence" value="0.73" />
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-3">Top 10 by HR probability</h2>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={top} layout="vertical" margin={{ left: 80 }}>
            <XAxis type="number" tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} stroke="var(--color-muted-foreground)" fontSize={12} />
            <YAxis type="category" dataKey="player" stroke="var(--color-muted-foreground)" fontSize={12} width={120} />
            <Tooltip formatter={(v: number) => `${(v * 100).toFixed(1)}%`} contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
            <Bar dataKey="pHr" radius={[0, 4, 4, 0]}>
              {top.map((r, i) => (<Cell key={i} fill={`oklch(${0.55 + i * 0.02} 0.18 ${20 + i * 8})`} />))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="rounded-lg border border-border bg-card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
            <tr>
              <th className="text-left px-4 py-3">#</th>
              <th className="text-left px-4 py-3">Player</th>
              <th className="text-left px-4 py-3">Matchup</th>
              <th className="text-left px-4 py-3">Park</th>
              <th className="text-right px-4 py-3">P(HR)</th>
              <th className="text-right px-4 py-3">Confidence</th>
              <th className="text-left px-4 py-3">Top drivers</th>
            </tr>
          </thead>
          <tbody>
            {rankings.map((r) => (
              <tr key={r.playerId} className="border-t border-border hover:bg-accent/40">
                <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{r.rank}</td>
                <td className="px-4 py-3">
                  <Link to="/player/$playerId" params={{ playerId: r.playerId }} className="font-medium hover:underline">{r.player}</Link>
                  <div className="text-xs text-muted-foreground">{r.team}</div>
                </td>
                <td className="px-4 py-3">
                  <Link to="/matchup" className="hover:underline">vs {r.opp}</Link>
                  <div className="text-xs text-muted-foreground">{r.pitcher}</div>
                </td>
                <td className="px-4 py-3 text-xs">{r.park}</td>
                <td className="px-4 py-3 text-right font-semibold tabular-nums">{(r.pHr * 100).toFixed(1)}%</td>
                <td className="px-4 py-3 text-right">
                  <div className="inline-flex items-center gap-2">
                    <div className="w-16 h-1.5 rounded-full bg-muted overflow-hidden">
                      <div className="h-full bg-emerald-500" style={{ width: `${r.confidence * 100}%` }} />
                    </div>
                    <span className="tabular-nums text-xs">{r.confidence.toFixed(2)}</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground">
                  {r.drivers.slice(0, 3).map((d) => d.name).join(" · ") || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </DashboardShell>
  );
}
