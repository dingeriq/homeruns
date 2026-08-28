import { Link, useRouterState } from "@tanstack/react-router";
import { BarChart3, Home, LineChart, Search, TrendingUp, Trophy, User } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { DemoBanner } from "@/components/demo-banner";
import { EnvBadge } from "@/components/env-badge";
import { ClientOnly } from "@/components/client-only";

const nav = [
  { to: "/", label: "Daily Rankings", icon: Trophy },
  { to: "/matchup", label: "Matchup Analysis", icon: Search },
  { to: "/performance", label: "Model Performance", icon: LineChart },
  { to: "/backtesting", label: "Backtesting", icon: TrendingUp },
  { to: "/features", label: "Feature Importance", icon: BarChart3 },
] as const;

export function DashboardShell({
  children,
  title,
  subtitle,
  slateDate,
}: {
  children: ReactNode;
  title: string;
  subtitle?: string;
  /** ISO date (YYYY-MM-DD) of the slate being displayed; falls back to today's UTC date. */
  slateDate?: string;
}) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  return (
    <div className="min-h-screen bg-background flex">
      <aside className="hidden md:flex w-64 shrink-0 flex-col border-r border-border bg-card">
        <div className="h-16 flex items-center gap-2 px-6 border-b border-border">
          <div className="w-8 h-8 rounded-md bg-primary flex items-center justify-center text-primary-foreground font-bold">⚾</div>
          <div>
            <div className="font-semibold text-sm">Dinger IQ</div>
            <div className="text-xs text-muted-foreground">MLB HR Predictor</div>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {nav.map((n) => {
            const active = pathname === n.to || (n.to !== "/" && pathname.startsWith(n.to));
            const Icon = n.icon;
            return (
              <Link
                key={n.to}
                to={n.to}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                  active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                <Icon className="w-4 h-4" />
                {n.label}
              </Link>
            );
          })}
        </nav>
        <div className="p-4 border-t border-border text-xs text-muted-foreground">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-emerald-500" />
            Model v1.4.2 · live
          </div>
        </div>
      </aside>
      <div className="flex-1 min-w-0 flex flex-col">
        <header className="h-16 border-b border-border bg-card px-6 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">{title}</h1>
            {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
          </div>
          <div className="flex items-center gap-3">
            <ClientOnly><EnvBadge /></ClientOnly>
            {slateDate ? (
              <span className="text-xs text-muted-foreground">
                {new Date(`${slateDate}T12:00:00Z`).toLocaleDateString("en-US", {
                  weekday: "long",
                  month: "short",
                  day: "numeric",
                  timeZone: "UTC",
                })}
              </span>
            ) : (
              <ClientOnly>
                <span className="text-xs text-muted-foreground">
                  {new Date().toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" })}
                </span>
              </ClientOnly>
            )}
          </div>

        </header>
        <main className="flex-1 p-6 space-y-6">
          <ClientOnly><DemoBanner /></ClientOnly>
          {children}
        </main>
      </div>
    </div>
  );
}

export function StatCard({ label, value, delta, tone = "default" }: { label: string; value: string; delta?: string; tone?: "default" | "positive" | "negative" }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-xs text-muted-foreground uppercase tracking-wide">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <div className="text-2xl font-semibold">{value}</div>
        {delta && (
          <span className={cn("text-xs font-medium", tone === "positive" && "text-emerald-500", tone === "negative" && "text-red-500", tone === "default" && "text-muted-foreground")}>
            {delta}
          </span>
        )}
      </div>
    </div>
  );
}
