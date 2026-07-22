import { useEffect, useState, type ReactNode } from "react";

/** Renders children only after client-side mount to avoid SSR hydration mismatch. */
export function ClientOnly({ children, fallback = null }: { children: ReactNode; fallback?: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return <>{mounted ? children : fallback}</>;
}
