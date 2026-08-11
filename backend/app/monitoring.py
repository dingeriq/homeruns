"""Application monitoring: Prometheus metrics + in-process runtime stats.

Exposes:
  * ``metrics_middleware``  — records latency/status for every HTTP request
  * ``GET /metrics``        — Prometheus text exposition format
  * ``GET /metrics/summary``— human/JSON friendly snapshot for dashboards

Nothing here can raise into the request path: every recording helper is
best-effort and swallows its own errors.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock
from typing import Any, Dict, Optional

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger("dingeriq.monitoring")

REGISTRY = CollectorRegistry(auto_describe=True)

http_requests_total = Counter(
    "dingeriq_http_requests_total",
    "Total HTTP requests handled.",
    ("method", "path", "status"),
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "dingeriq_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ("method", "path"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)

http_requests_in_progress = Gauge(
    "dingeriq_http_requests_in_progress",
    "HTTP requests currently being served.",
    registry=REGISTRY,
)

http_exceptions_total = Counter(
    "dingeriq_http_exceptions_total",
    "Unhandled exceptions raised while serving a request.",
    ("path",),
    registry=REGISTRY,
)

job_runs_total = Counter(
    "dingeriq_job_runs_total",
    "Background job executions by outcome.",
    ("job", "outcome"),
    registry=REGISTRY,
)

job_duration_seconds = Histogram(
    "dingeriq_job_duration_seconds",
    "Background job duration in seconds.",
    ("job",),
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800),
    registry=REGISTRY,
)

job_last_success_timestamp = Gauge(
    "dingeriq_job_last_success_timestamp_seconds",
    "Unix timestamp of the last successful run of a job.",
    ("job",),
    registry=REGISTRY,
)

records_synced_total = Counter(
    "dingeriq_records_synced_total",
    "Rows written by sync jobs, by entity.",
    ("entity",),
    registry=REGISTRY,
)

database_up = Gauge(
    "dingeriq_database_up",
    "1 when the last database health check succeeded, 0 otherwise.",
    registry=REGISTRY,
)

database_connection_checks_total = Counter(
    "dingeriq_database_connection_checks_total",
    "Database connectivity checks by outcome.",
    ("outcome",),
    registry=REGISTRY,
)

database_connection_failures_total = Counter(
    "dingeriq_database_connection_failures_total",
    "Database connection failures (ping or query).",
    ("operation",),
    registry=REGISTRY,
)

mlb_api_requests_total = Counter(
    "dingeriq_mlb_api_requests_total",
    "Requests to the MLB Stats API by endpoint and outcome.",
    ("endpoint", "outcome"),
    registry=REGISTRY,
)

mlb_api_request_failures_total = Counter(
    "dingeriq_mlb_api_request_failures_total",
    "Failed MLB Stats API requests by endpoint and error class.",
    ("endpoint", "error"),
    registry=REGISTRY,
)

mlb_api_request_duration_seconds = Histogram(
    "dingeriq_mlb_api_request_duration_seconds",
    "MLB Stats API request latency in seconds.",
    ("endpoint",),
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20),
    registry=REGISTRY,
)

sync_last_start_timestamp = Gauge(
    "dingeriq_sync_last_start_timestamp_seconds",
    "Unix timestamp when a sync job last started.",
    ("job",),
    registry=REGISTRY,
)

sync_last_end_timestamp = Gauge(
    "dingeriq_sync_last_end_timestamp_seconds",
    "Unix timestamp when a sync job last finished (success or failure).",
    ("job",),
    registry=REGISTRY,
)

sync_last_duration_seconds = Gauge(
    "dingeriq_sync_last_duration_seconds",
    "Duration of the most recent run of a sync job.",
    ("job",),
    registry=REGISTRY,
)

sync_in_progress = Gauge(
    "dingeriq_sync_in_progress",
    "1 while a sync job is running.",
    ("job",),
    registry=REGISTRY,
)

sync_last_entity_count = Gauge(
    "dingeriq_sync_last_entity_count",
    "Rows written by the most recent sync, per entity (games, teams, players).",
    ("entity",),
    registry=REGISTRY,
)

scheduler_up = Gauge(
    "dingeriq_scheduler_up",
    "1 when the APScheduler instance is running.",
    registry=REGISTRY,
)

initial_sync_status = Gauge(
    "dingeriq_initial_sync_status",
    "Initial sync state: 0=pending, 1=running, 2=complete, 3=failed.",
    registry=REGISTRY,
)

schema_ready = Gauge(
    "dingeriq_schema_ready",
    "1 when the database schema has been created/verified.",
    registry=REGISTRY,
)

process_start_time = Gauge(
    "dingeriq_process_start_time_seconds",
    "Unix timestamp of process start.",
    registry=REGISTRY,
)
process_start_time.set(time.time())

_STARTED_AT = time.time()

INITIAL_SYNC_STATES = {"pending": 0, "running": 1, "complete": 2, "failed": 3}

# --- lightweight in-process snapshot (for /metrics/summary) -----------------

_lock = Lock()
_request_count = 0
_error_count = 0
_latency_sum = 0.0
_status_counts: Dict[str, int] = defaultdict(int)
_status_class_counts: Dict[str, int] = defaultdict(int)
_route_stats: Dict[str, Dict[str, float]] = {}
_route_counts: Dict[str, int] = defaultdict(int)
_jobs: Dict[str, Dict[str, Any]] = {}
_db_failures = 0
_mlb_failures = 0
_entity_counts: Dict[str, int] = {}



def normalize_path(raw_path: str, route_path: Optional[str] = None) -> str:
    """Use the templated route path so IDs don't explode metric cardinality."""
    if route_path:
        return route_path
    parts = []
    for segment in raw_path.split("/"):
        parts.append(":id" if segment.isdigit() else segment)
    return "/".join(parts) or "/"


def record_request(method: str, path: str, status: int, duration: float) -> None:
    global _request_count, _error_count, _latency_sum
    try:
        http_requests_total.labels(method=method, path=path, status=str(status)).inc()
        http_request_duration_seconds.labels(method=method, path=path).observe(duration)
        with _lock:
            _request_count += 1
            _latency_sum += duration
            if status >= 500:
                _error_count += 1
            _status_counts[str(status)] += 1
            _route_counts[f"{method} {path}"] += 1
    except Exception:  # pragma: no cover - metrics must never break a request
        logger.debug("Failed to record request metrics", exc_info=True)


def record_exception(path: str) -> None:
    try:
        http_exceptions_total.labels(path=path).inc()
    except Exception:  # pragma: no cover
        logger.debug("Failed to record exception metric", exc_info=True)


def record_database_up(up: bool) -> None:
    try:
        database_up.set(1 if up else 0)
    except Exception:  # pragma: no cover
        logger.debug("Failed to record database gauge", exc_info=True)


def record_synced_counts(counts: Any) -> None:
    """Accept the dict returned by sync jobs and count rows per entity."""
    if not isinstance(counts, dict):
        return
    for entity, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            records_synced_total.labels(entity=str(entity)).inc(float(value))
        except Exception:  # pragma: no cover
            logger.debug("Failed to record sync counts", exc_info=True)


class track_job:
    """Context manager recording duration + outcome of a background job."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._start = 0.0

    def __enter__(self) -> "track_job":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - self._start
        outcome = "failure" if exc_type else "success"
        try:
            job_runs_total.labels(job=self.name, outcome=outcome).inc()
            job_duration_seconds.labels(job=self.name).observe(duration)
            if outcome == "success":
                job_last_success_timestamp.labels(job=self.name).set(time.time())
            with _lock:
                _jobs[self.name] = {
                    "last_outcome": outcome,
                    "last_duration_seconds": round(duration, 3),
                    "last_run_at": time.time(),
                    "last_error": str(exc) if exc else None,
                }
        except Exception:  # pragma: no cover
            logger.debug("Failed to record job metrics", exc_info=True)
        return False  # never suppress the exception


def render_prometheus() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def summary() -> Dict[str, Any]:
    with _lock:
        requests = _request_count
        errors = _error_count
        latency_sum = _latency_sum
        statuses = dict(_status_counts)
        routes = dict(sorted(_route_counts.items(), key=lambda kv: -kv[1])[:10])
        jobs = {k: dict(v) for k, v in _jobs.items()}
    return {
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "requests_total": requests,
        "errors_total": errors,
        "error_rate": round(errors / requests, 4) if requests else 0.0,
        "avg_latency_ms": round((latency_sum / requests) * 1000, 2) if requests else 0.0,
        "status_counts": statuses,
        "top_routes": routes,
        "jobs": jobs,
    }
