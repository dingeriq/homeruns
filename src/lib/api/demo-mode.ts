// SSR-safe demo mode store using useSyncExternalStore.
// Initial server snapshot is always { isDemoMode: false, bannerDismissed: false }
// so the SSR HTML matches the first client render (no hydration mismatch).

import { useSyncExternalStore } from "react";

type State = {
  isDemoMode: boolean;
  bannerDismissed: boolean;
};

let state: State = { isDemoMode: false, bannerDismissed: false };
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function getSnapshot(): State {
  return state;
}

const serverSnapshot: State = { isDemoMode: false, bannerDismissed: false };
function getServerSnapshot(): State {
  return serverSnapshot;
}

export function enableDemoMode(reason?: string) {
  if (state.isDemoMode) return;
  if (reason) console.warn("[demo-mode] enabled:", reason);
  // Fresh outage → show the banner again even if it was previously dismissed.
  state = { isDemoMode: true, bannerDismissed: false };
  emit();
}

export function disableDemoMode() {
  if (!state.isDemoMode) return;
  console.info("[demo-mode] disabled — backend healthy");
  state = { isDemoMode: false, bannerDismissed: false };
  emit();
}

export function dismissBanner() {
  if (state.bannerDismissed) return;
  state = { ...state, bannerDismissed: true };
  emit();
}

export function isDemoMode(): boolean {
  return state.isDemoMode;
}

export function useDemoMode(): State {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
