// Local time display helpers.
//
// The backend is canonical UTC: every game instant arrives as an ISO 8601
// timestamp with an offset. These helpers convert that absolute instant into
// the *user's own device timezone* using Intl, which resolves the IANA zone and
// its daylight-saving rules automatically — no fixed EST/EDT offset anywhere.
//
// Display time is never used to decide whether a game has started; that
// decision belongs to the backend's first-pitch lock.

/** The viewer's IANA timezone, e.g. "America/Los_Angeles". */
export function userTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function parse(iso?: string | null): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * "7:10 PM EDT" — clock time in the viewer's timezone plus the zone
 * abbreviation for that exact instant (so DST is reflected correctly).
 */
export function formatGameTime(iso?: string | null, timeZone?: string): string {
  const d = parse(iso);
  if (!d) return "—";
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
    ...(timeZone ? { timeZone } : {}),
  }).format(d);
}

/** The viewer-local calendar date of an absolute instant, as YYYY-MM-DD. */
export function localDateOf(iso?: string | null, timeZone?: string): string {
  const d = parse(iso);
  if (!d) return "";
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(timeZone ? { timeZone } : {}),
  }).format(d);
  return parts;
}

/** The viewer's current local calendar date, as YYYY-MM-DD. */
export function localToday(now: Date = new Date(), timeZone?: string): string {
  return localDateOf(now.toISOString(), timeZone);
}

/**
 * Render a slate date (YYYY-MM-DD, already the authoritative baseball day) as a
 * readable label. Parsed as a plain calendar date, never shifted by timezone.
 */
export function formatSlateDate(slateDate?: string | null): string {
  if (!slateDate) return "";
  const [y, m, d] = slateDate.split("-").map(Number);
  if (!y || !m || !d) return slateDate;
  return new Date(Date.UTC(y, m - 1, d, 12)).toLocaleDateString("en-US", {
    weekday: "long",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** "Today" / "Tomorrow" relative to the *viewer's* local calendar date. */
export function relativeDayLabel(
  slateDate?: string | null,
  now: Date = new Date(),
  timeZone?: string,
): string | null {
  if (!slateDate) return null;
  const today = localToday(now, timeZone);
  if (slateDate === today) return "Today";
  const tomorrowMs = Date.parse(`${today}T12:00:00Z`) + 86_400_000;
  if (slateDate === new Date(tomorrowMs).toISOString().slice(0, 10)) return "Tomorrow";
  return null;
}
