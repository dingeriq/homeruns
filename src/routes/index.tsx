import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { ErrorPanel, LoadingPanel, SkeletonCard, SkeletonRow } from "@/components/query-states";
import { slateSummaryQuery, topCandidatesQuery } from "@/lib/api/queries";
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
  const rankingsQ = useQuery(topCandidatesQuery(25));
  const summaryQ = useQuery(slateSummaryQuery());

  const rankings = rankingsQ.data ?? [];
  const top = rankings.slice(0, 10);

  return (
    <DashboardShell title="Daily Home Run Rankings" subtitle="Top 25 candidates from today's slate">
      {summaryQ.isError ? (
        <ErrorPanel error={summaryQ.error} onRetry={() => summaryQ.refetch()} />
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Slate size" value={summaryQ.isLoading ? "…" : `${summaryQ.data?.games ?? 0} games`} />
          <StatCard label="Hitters scored" value={summaryQ.isLoading ? "…" : (summaryQ.data?.hitters_scored ?? 0).toString()} />
          <StatCard
            label="Top prob"
            value={summaryQ.isLoading ? "…" : `${((summaryQ.data?.top_prob ?? 0) * 100).toFixed(1)}%`}
            delta={summaryQ.data?.top_prob_player}
            tone="positive"
          />
          <StatCard label="Avg confidence" value={summaryQ.isLoading ? "…" : (summaryQ.data?.avg_confidence ?? 0).toFixed(2)} />
        </div>
      )}

      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-3">Top 10 by HR probability</h2>
        {rankingsQ.isLoading ? (
          <SkeletonCard height={260} />
        ) : rankingsQ.isError ? (
          <ErrorPanel error={rankingsQ.error} onRetry={() => rankingsQ.refetch()} />
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={top} layout="vertical" margin={{ left: 80 }}>
              <XAxis type="number" tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} stroke="var(--color-muted-foreground)" fontSize={12} />
              <YAxis type="category" dataKey="player" stroke="var(--color-muted-foreground)" fontSize={12} width={120} />
              <Tooltip formatter={(v: number) => `${(v * 100).toFixed(1)}%`} contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
              <Bar dataKey="p_hr" radius={[0, 4, 4, 0]}>
                {top.map((_, i) => (<Cell key={i} fill={`oklch(${0.55 + i * 0.02} 0.18 ${20 + i * 8})`} />))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
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
            {rankingsQ.isLoading &&
              Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={7} />)}
            {rankingsQ.isError && (
              <tr>
                <td colSpan={7} className="p-4">
                  <ErrorPanel error={rankingsQ.error} onRetry={() => rankingsQ.refetch()} />
                </td>
              </tr>
            )}
            {!rankingsQ.isLoading && !rankingsQ.isError && rankings.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">
                  No predictions available for today's slate.
                </td>
              </tr>
            )}
            {rankings.map((r) => (
              <tr key={r.player_id} className="border-t border-border hover:bg-accent/40">
                <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{r.rank}</td>
                <td className="px-4 py-3">
                  <Link to="/player/$playerId" params={{ playerId: r.player_id }} className="font-medium hover:underline">{r.player}</Link>
                  <div className="text-xs text-muted-foreground">{r.team}</div>
                </td>
                <td className="px-4 py-3">
                  <Link to="/matchup" className="hover:underline">vs {r.opp}</Link>
                  <div className="text-xs text-muted-foreground">{r.pitcher}</div>
                </td>
                <td className="px-4 py-3 text-xs">{r.park}</td>
                <td className="px-4 py-3 text-right font-semibold tabular-nums">{(r.p_hr * 100).toFixed(1)}%</td>
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
