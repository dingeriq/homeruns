import { createFileRoute } from "@tanstack/react-router";
import { DashboardShell, StatCard } from "@/components/dashboard-shell";
import { Bar, BarChart, ResponsiveContainer, XAxis, YAxis, Tooltip, Legend } from "recharts";

export const Route = createFileRoute("/matchup")({
  head: () => ({ meta: [{ title: "Matchup Analysis — Dinger IQ" }] }),
  component: Matchup,
});

const pitchTypes = [
  { pitch: "4-Seam", batter: 0.412, league: 0.325 },
  { pitch: "Slider", batter: 0.298, league: 0.301 },
  { pitch: "Curveball", batter: 0.221, league: 0.278 },
  { pitch: "Changeup", batter: 0.361, league: 0.311 },
  { pitch: "Sinker", batter: 0.389, league: 0.318 },
  { pitch: "Cutter", batter: 0.334, league: 0.306 },
];

function Matchup() {
  return (
    <DashboardShell title="Matchup Analysis" subtitle="Aaron Judge (NYY) vs Kutter Crawford (BOS)">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Batter</div>
          <div className="mt-1 font-semibold">Aaron Judge · R</div>
          <div className="text-xs text-muted-foreground">NYY · Batting 2nd</div>
          <div className="mt-3 space-y-1 text-sm">
            <Row k="vs RHP wOBA" v=".398" />
            <Row k="vs RHP ISO" v=".274" />
            <Row k="Barrel% 14d" v="18.5%" />
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Pitcher</div>
          <div className="mt-1 font-semibold">Kutter Crawford · R</div>
          <div className="text-xs text-muted-foreground">BOS · Season xERA 4.62</div>
          <div className="mt-3 space-y-1 text-sm">
            <Row k="HR/9" v="1.62" />
            <Row k="Barrel% allowed" v="9.1%" />
            <Row k="FB% allowed" v="41.2%" />
          </div>
        </div>
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-xs uppercase text-muted-foreground">Environment</div>
          <div className="mt-1 font-semibold">Yankee Stadium</div>
          <div className="text-xs text-muted-foreground">7:05 PM · Clear</div>
          <div className="mt-3 space-y-1 text-sm">
            <Row k="Park HR factor (R)" v="112" />
            <Row k="Wind to CF" v="8 mph out" />
            <Row k="Temp / Humidity" v="78°F · 54%" />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="P(HR)" value="28.4%" tone="positive" delta="+3.1% vs slate" />
        <StatCard label="Expected PA" value="4.2" />
        <StatCard label="Vegas team total" value="5.5" />
        <StatCard label="Confidence" value="0.91" tone="positive" />
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-semibold mb-3">Pitch-type xSLG — batter vs league</h2>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={pitchTypes}>
            <XAxis dataKey="pitch" stroke="var(--color-muted-foreground)" fontSize={12} />
            <YAxis stroke="var(--color-muted-foreground)" fontSize={12} />
            <Tooltip contentStyle={{ background: "var(--color-card)", border: "1px solid var(--color-border)", borderRadius: 6 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="batter" fill="oklch(0.65 0.2 25)" name="Judge xSLG" radius={[4, 4, 0, 0]} />
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
