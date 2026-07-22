
## Goal

Stop the "VITE_API_BASE_URL is not configured" crash, default to `http://localhost:8000`, gracefully degrade to mock data with a dismissible banner + env badge, and auto-recover from Demo Mode when the backend comes back.

## Files changed

**New**
- `.env.example` — `VITE_API_BASE_URL=http://localhost:8000`
- `src/lib/api/config.ts` — single source of truth:
  - `API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"`
  - `API_KEY = import.meta.env.VITE_API_KEY`
  - `getEnvLabel()` → `"Local" | "Development" | "Production"` from hostname + base URL (client-only; SSR returns `"Local"` as a stable default to avoid hydration mismatch).
- `src/lib/api/demo-mode.ts` — SSR-safe store via `useSyncExternalStore`:
  - state: `isDemoMode`, `bannerDismissed`
  - actions: `enableDemoMode(reason)`, `disableDemoMode()`, `dismissBanner()`
  - initial server snapshot is always `false` so hydration matches.
- `src/lib/api/mock-data.ts` — minimal fixtures for each DTO in `queries.ts` (rankings, slate summary, player, featured matchup, model performance, feature importance, backtest).
- `src/lib/api/health.ts` — `checkHealth()` does `fetch(\`${API_BASE_URL}/health\`)` with a 3s `AbortController` timeout; returns `boolean`.
- `src/components/health-monitor.tsx` — client-only component mounted in root:
  - On mount: run `checkHealth()`; on failure call `enableDemoMode("health check failed")` and `console.warn` the error.
  - When `isDemoMode` is true: `setInterval(checkHealth, 30_000)`. On success → `disableDemoMode()`, `queryClient.invalidateQueries()` (invalidates and refetches all active queries by default), clear the interval.
  - Cleanup on unmount.
- `src/components/demo-banner.tsx` — dismissible banner "Backend unavailable. Running in demo mode." Visible only when `isDemoMode && !bannerDismissed`. Auto-hides when demo mode is disabled (state re-read via the store).
- `src/components/env-badge.tsx` — pill in the header. Shows `Demo Mode` when active, otherwise `getEnvLabel()`.

**Modified**
- `src/lib/api/client.ts` — import from `config.ts`; drop the "not configured" throw. On fetch/network failure, call `enableDemoMode(reason)` then rethrow (so React Query records the error; mock fallback happens one layer up).
- `src/lib/api/queries.ts` — each `queryFn` wraps `apiFetch` in try/catch; on failure, return the matching mock fixture. Keeps `queryOptions` signatures unchanged so no route needs edits.
- `src/components/dashboard-shell.tsx` — render `<DemoBanner />` above content, `<EnvBadge />` in the header row.
- `src/routes/__root.tsx` — mount `<HealthMonitor />` inside `RootComponent` (needs access to `queryClient` via `useQueryClient()`, so it lives inside `QueryClientProvider`). Client-only effect; SSR renders nothing extra.

## Recovery flow

1. API call fails or startup `/health` fails → `enableDemoMode()` → banner + badge switch → queries serve mock data.
2. `HealthMonitor` polls `/health` every 30s while demo mode is on.
3. First successful `/health`:
   - `disableDemoMode()` — banner and `Demo Mode` badge disappear; badge reverts to `Local`/`Development`/`Production`.
   - `queryClient.invalidateQueries()` — marks every query stale; active queries refetch automatically, inactive ones refetch on next mount.
   - Polling interval cleared.
4. If a later request fails again, demo mode re-enables and polling restarts.

## Technical details

- Polling uses `setInterval` inside a `useEffect` gated on `isDemoMode`; cleared on unmount and when demo mode turns off. Only one interval ever runs.
- `checkHealth` uses `AbortController` with a 3s timeout so a hung backend never blocks recovery attempts.
- `bannerDismissed` resets to `false` when demo mode re-enables, so a fresh outage shows the banner again.
- All demo-mode state and health polling are client-only; SSR HTML is deterministic (banner hidden, badge = `Local`) which keeps hydration clean.

## Not included

- The existing `Wednesday, Jul 22` vs `Tuesday, Jul 21` hydration mismatch in `dashboard-shell.tsx` (server/client `new Date()`) is a separate pre-existing bug. Say the word and I'll bundle the fix.
