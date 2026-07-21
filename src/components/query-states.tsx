import { AlertCircle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function LoadingPanel({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center justify-center gap-2 rounded-lg border border-border bg-card p-6 text-sm text-muted-foreground",
        className,
      )}
      role="status"
      aria-live="polite"
    >
      <Loader2 className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

export function ErrorPanel({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div
      className={cn(
        "rounded-lg border border-red-500/40 bg-red-500/5 p-4 text-sm",
        className,
      )}
      role="alert"
    >
      <div className="flex items-start gap-2">
        <AlertCircle className="mt-0.5 h-4 w-4 text-red-500" />
        <div className="flex-1">
          <div className="font-medium text-red-500">Failed to load data</div>
          <div className="mt-1 text-xs text-muted-foreground break-words">{message}</div>
          {onRetry && (
            <button
              onClick={onRetry}
              className="mt-3 inline-flex items-center rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-accent"
            >
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function SkeletonRow({ cols = 6 }: { cols?: number }) {
  return (
    <tr className="border-t border-border">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="h-3 w-full max-w-24 animate-pulse rounded bg-muted" />
        </td>
      ))}
    </tr>
  );
}

export function SkeletonCard({ height = 260 }: { height?: number }) {
  return (
    <div
      className="animate-pulse rounded-lg border border-border bg-card"
      style={{ height }}
      aria-hidden
    />
  );
}
