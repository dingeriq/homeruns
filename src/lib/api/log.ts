// Temporary instrumentation for the live-data migration.
// Logs every API read: endpoint, record count, and the data origin.

export type DataSource = "postgres" | "mock";

export function logApi(endpoint: string, count: number, source: DataSource, note?: string) {
  const tag = source === "postgres" ? "PostgreSQL" : "Mock data";
  // eslint-disable-next-line no-console
  console.info(
    `[api] ${endpoint} → ${count} record${count === 1 ? "" : "s"} · source: ${tag}${note ? ` · ${note}` : ""}`,
  );
}
