"""Read-only PostgreSQL resource-usage assessment.

Pure inspection: no DDL, no writes, no data mutation. Used to answer
"what resource is actually being exhausted?" before touching plans or config.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from sqlalchemy import text

from app.database.session import get_engine, session_scope

logger = logging.getLogger("dingeriq.db_usage")


def _rows(sql: str, **params: Any) -> List[Dict[str, Any]]:
    with session_scope() as s:
        res = s.execute(text(sql), params)
        return [dict(r._mapping) for r in res]


def _one(sql: str, **params: Any) -> Dict[str, Any]:
    out = _rows(sql, **params)
    return out[0] if out else {}


def run_db_usage() -> Dict[str, Any]:
    report: Dict[str, Any] = {}

    report["database"] = _one(
        """
        SELECT current_database() AS name,
               pg_database_size(current_database()) AS size_bytes,
               pg_size_pretty(pg_database_size(current_database())) AS size_pretty
        """
    )

    report["tables"] = _rows(
        """
        SELECT relname AS table,
               n_live_tup AS approx_rows,
               pg_total_relation_size(relid) AS total_bytes,
               pg_size_pretty(pg_total_relation_size(relid)) AS total_pretty,
               pg_size_pretty(pg_relation_size(relid)) AS heap_pretty,
               pg_size_pretty(pg_indexes_size(relid)) AS index_pretty,
               pg_indexes_size(relid) AS index_bytes
        FROM pg_stat_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
        LIMIT 25
        """
    )

    report["indexes"] = _rows(
        """
        SELECT indexrelname AS index,
               relname AS table,
               idx_scan AS scans,
               pg_size_pretty(pg_relation_size(indexrelid)) AS size_pretty,
               pg_relation_size(indexrelid) AS size_bytes
        FROM pg_stat_user_indexes
        ORDER BY pg_relation_size(indexrelid) DESC
        LIMIT 25
        """
    )

    report["connections"] = _one(
        """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE state = 'active') AS active,
               count(*) FILTER (WHERE state = 'idle') AS idle,
               count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction,
               current_setting('max_connections')::int AS max_connections
        FROM pg_stat_activity
        WHERE datname = current_database()
        """
    )

    report["long_running"] = _rows(
        """
        SELECT pid,
               state,
               EXTRACT(EPOCH FROM (now() - query_start))::int AS seconds,
               wait_event_type,
               wait_event,
               left(query, 160) AS query
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND state <> 'idle'
          AND query_start IS NOT NULL
          AND now() - query_start > interval '30 seconds'
        ORDER BY query_start
        LIMIT 20
        """
    )

    report["activity"] = _one(
        """
        SELECT xact_commit, xact_rollback, tup_inserted, tup_updated, tup_deleted,
               blks_read, blks_hit,
               CASE WHEN blks_read + blks_hit = 0 THEN NULL
                    ELSE round(blks_hit::numeric / (blks_read + blks_hit), 4) END AS cache_hit_ratio,
               deadlocks, temp_files, temp_bytes
        FROM pg_stat_database
        WHERE datname = current_database()
        """
    )

    report["statcast_pitches"] = _one(
        """
        SELECT count(*) AS rows,
               min(game_date)::text AS first_date,
               max(game_date)::text AS last_date,
               count(*) FILTER (WHERE game_date >= DATE '2024-01-01'
                                  AND game_date <  DATE '2025-01-01') AS rows_2024
        FROM statcast_pitches
        """
    )

    engine = get_engine()
    pool = engine.pool
    report["app_pool"] = {
        "size": getattr(pool, "size", lambda: None)(),
        "checked_out": getattr(pool, "checkedout", lambda: None)(),
        "overflow": getattr(pool, "overflow", lambda: None)(),
        "max_overflow": getattr(pool, "_max_overflow", None),
    }

    return report
