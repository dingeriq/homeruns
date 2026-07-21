import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { ErrorPanel, LoadingPanel } from "@/components/query-states";
import { featuredMatchupQuery } from "@/lib/api/queries";
import { Bar, BarChart, ResponsiveContainer, XAxis, YAxis, Tooltip, Legend } from "recharts";

export const Route = createFileRoute("/matchup")({
  head: () => ({ meta: [{ title: "Matchup Analysis — Dinger IQ" }] }),
  component: Matchup,
});

function Matchup() {
  const q = useQuery(featuredMatchupQuery());

  if (q.isLoading) {
    return (
      <DashboardShell title="Matchup Analysis" subtitle="Loading featured matchup…">
        <LoadingPanel />
      </DashboardShell>
    );
  }
  if (q.isError || !q.data) {
    return (
      <DashboardShell title="Matchup Analysis" subtitle="—">
        <ErrorPanel error={q.error ?? new Error("Matchup unavailable")} onRetry={() => q.refetch()} />
      </DashboardShell>
    );
  }

  const g = q.data;
  const pitchTypes = g.pitch_types.map((pt) => ({
    pitch: pt.pitch,
    batter: pt.batter_x_slg,
    league: pt.league_x_slg,
  }));

  return (
    <DashboardShell title="Matchup Analysis" subtitle={`${g.batter.player} (${g.batter.team}) vs ${g.pitcher.name} (${g.away === g.batter.team ? g.home : g.away})`}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Batter</div>
          <div className="mt-1 font-semibold">{g.batter.player} · {g.batter.hand}</div>
          <div className="text-xs text-muted-foreground">{g.batter.team} · Batting {g.batter.order}</div>
          <div className="mt-3 space-y-1 text-sm">
            <Row k={`vs ${g.pitcher.hand}HP wOBA`} v={g.batter.vs_hand_woba.toFixed(3)} />
            <Row k={`vs ${g.pitcher.hand}HP ISO`} v={g.batter.vs_hand_iso.toFixed(3)} />
            <Row k="Barrel% 14d" v="—" />
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Pitcher</div>
          <div className="mt-1 font-semibold">{g.pitcher.name} · {g.pitcher.hand}</div>
          <div className="text-xs text-muted-foreground">Season xERA {g.pitcher.season_x_era.toFixed(2)}</div>
          <div className="mt-3 space-y-1 text-sm">
            <Row k="HR/9" v={g.pitcher.hr_per_9.toFixed(2)} />
            <Row k="Barrel% allowed" v={`${(g.pitcher.barrel_pct_allowed * 100).toFixed(1)}%`} />
            <Row k="FB% allowed" v={`${(g.pitcher.fb_pct_allowed * 100).toFixed(1)}%`} />
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Environment</div>
          <div className="mt-1 font-semibold">{g.park}</div>
          <div className="text-xs text-muted-foreground">{g.first_pitch} · {g.weather.conditions}</div>
          <div className="mt-3 space-y-1 text-sm">
            <Row k={`Park HR factor (${g.batter.hand})`} v={String(g.batter.hand === "L" ? g.park_factors.hr_factor_l : g.park_factors.hr_factor_r)} />
            <Row k="Wind" v={`${g.weather.wind_mph} mph ${g.weather.wind_dir}`} />
            <Row k="Temp / Humidity" v={`${g.weather.temp_f}°F · ${g.weather.humidity}%`} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="P(HR)" value={`${(g.batter.p_hr * 100).toFixed(1)}%`} tone="positive" />
        <StatCard label="Expected PA" value="4.2" />
        <StatCard label="Vegas team total" value={g.vegas.home_total.toFixed(1)} />
        <StatCard label="Confidence" value={g.batter.confidence.toFixed(2)} tone="positive" />
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-3">Pitch-type xSLG — batter vs league</h2>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={pitchTypes}>
            <XAxis dataKey="pitch" stroke="var(--color-muted-foreground)" fontSize={12} />
            <YAxis stroke="var(--color-muted-foreground)" fontSize={12} />
            <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="batter" fill="oklch(0.65 0.2 25)" name={`${g.batter.player} xSLG`} radius={[4, 4, 0, 0]} />
            <Bar dataKey="league" fill="oklch(0.6 0.05 260)" name="League avg" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </DashboardShell>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between border-b border-border/50 pb-1">
      <span className="text-muted-foreground text-xs">{k}</span>
      <span className="tabular-nums text-xs font-medium">{v}</span>
    </div>
  );
}
