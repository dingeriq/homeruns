import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { ErrorPanel, LoadingPanel } from "@/components/query-states";
import { playerQuery } from "@/lib/api/queries";
import { Area, AreaChart, ResponsiveContainer, XAxis, YAxis, Tooltip, Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis } from "recharts";

export const Route = createFileRoute("/player/$playerId")({
  head: ({ params }) => ({
    meta: [{ title: `Player · ${params.playerId} — Dinger IQ` }],
  }),
  component: PlayerPage,
});

function PlayerPage() {
  const { playerId } = Route.useParams();
  const q = useQuery(playerQuery(playerId));

  if (q.isLoading) {
    return (
      <DashboardShell title="Loading player…" subtitle={playerId}>
        <LoadingPanel label="Fetching player predictions and stats…" />
      </DashboardShell>
    );
  }
  if (q.isError || !q.data) {
    return (
      <DashboardShell title="Player" subtitle={playerId}>
        <Link to="/" className="text-xs text-muted-foreground hover:text-foreground">← Back to rankings</Link>
        <ErrorPanel error={q.error ?? new Error("Player not found")} onRetry={() => q.refetch()} />
      </DashboardShell>
    );
  }

  const p = q.data;
  const radar = [
    { k: "Barrel%", v: p.stats.barrel_14 * 100 },
    { k: "Hard Hit%", v: p.stats.hard_hit * 100 },
    { k: "Pull%", v: p.stats.pull * 100 },
    { k: "FB%", v: p.stats.fly_ball * 100 },
    { k: "ISO×100", v: p.stats.iso * 100 },
    { k: "xwOBA×100", v: p.stats.x_woba * 100 },
  ];

  return (
    <DashboardShell title={p.player} subtitle={`${p.team} · vs ${p.opp} @ ${p.park}`}>
      <Link to="/" className="text-xs text-muted-foreground hover:text-foreground">← Back to rankings</Link>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Today P(HR)" value={`${(p.p_hr * 100).toFixed(1)}%`} delta={`rank #${p.rank}`} tone="positive" />
        <StatCard label="Confidence" value={p.confidence.toFixed(2)} />
        <StatCard label="Barrel% 14d" value={`${(p.stats.barrel_14 * 100).toFixed(1)}%`} />
        <StatCard label="xwOBA 30d" value={p.stats.x_woba.toFixed(3)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3">Last 30 days · predicted vs actual HR</h2>
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={p.last_30}>
              <defs>
                <linearGradient id="pHr" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="oklch(0.65 0.2 25)" stopOpacity={0.6} />
                  <stop offset="100%" stopColor="oklch(0.65 0.2 25)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="day" stroke="var(--color-muted-foreground)" fontSize={12} />
              <YAxis stroke="var(--color-muted-foreground)" fontSize={12} tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} />
              <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} formatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
              <Area type="monotone" dataKey="p_hr" stroke="oklch(0.65 0.2 25)" fill="url(#pHr)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3">Skill radar</h2>
          <ResponsiveContainer width="100%" height={260}>
            <RadarChart data={radar}>
              <PolarGrid stroke="var(--color-border)" />
              <PolarAngleAxis dataKey="k" tick={{ fontSize: 11, fill: "var(--color-muted-foreground)" }} />
              <PolarRadiusAxis tick={{ fontSize: 10 }} />
              <Radar dataKey="v" stroke="oklch(0.65 0.2 25)" fill="oklch(0.65 0.2 25)" fillOpacity={0.4} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3 text-emerald-500">Top positive drivers</h2>
          <ul className="space-y-2">
            {p.drivers.length === 0 && <li className="text-xs text-muted-foreground">No meaningful positives.</li>}
            {p.drivers.map((d) => (
              <li key={d.name} className="flex items-center justify-between text-sm">
                <span>{d.name}</span>
                <span className="tabular-nums text-emerald-500">+{(d.value * 100).toFixed(2)}%</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <h2 className="text-sm font-semibold mb-3 text-red-500">Top negative drivers</h2>
          <ul className="space-y-2">
            {p.negatives.length === 0 && <li className="text-xs text-muted-foreground">No meaningful negatives.</li>}
            {p.negatives.map((d) => (
              <li key={d.name} className="flex items-center justify-between text-sm">
                <span>{d.name}</span>
                <span className="tabular-nums text-red-500">{(d.value * 100).toFixed(2)}%</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </DashboardShell>
  );
}
