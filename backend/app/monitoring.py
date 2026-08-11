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


def _status_class(status: int) -> str:
    return f"{max(status // 100, 1)}xx"


def record_request(method: str, path: str, status: int, duration: float) -> None:
    global _request_count, _error_count, _latency_sum
    try:
        http_requests_total.labels(method=method, path=path, status=str(status)).inc()
        http_request_duration_seconds.labels(method=method, path=path).observe(duration)
        key = f"{method} {path}"
        with _lock:
            _request_count += 1
            _latency_sum += duration
            if status >= 500:
                _error_count += 1
            _status_counts[str(status)] += 1
            _status_class_counts[_status_class(status)] += 1
            _route_counts[key] += 1
            stat = _route_stats.setdefault(
                key, {"count": 0.0, "latency_sum": 0.0, "max_latency": 0.0, "errors": 0.0}
            )
            stat["count"] += 1
            stat["latency_sum"] += duration
            stat["max_latency"] = max(stat["max_latency"], duration)
            if status >= 500:
                stat["errors"] += 1
    except Exception:  # pragma: no cover - metrics must never break a request
        logger.debug("Failed to record request metrics", exc_info=True)


def record_exception(path: str) -> None:
    try:
        http_exceptions_total.labels(path=path).inc()
    except Exception:  # pragma: no cover
        logger.debug("Failed to record exception metric", exc_info=True)


def record_database_up(up: bool, operation: str = "ping") -> None:
    """Record the outcome of a database connectivity check."""
    global _db_failures
    try:
        database_up.set(1 if up else 0)
        database_connection_checks_total.labels(outcome="success" if up else "failure").inc()
        if not up:
            database_connection_failures_total.labels(operation=operation).inc()
            with _lock:
                _db_failures += 1
    except Exception:  # pragma: no cover
        logger.debug("Failed to record database metrics", exc_info=True)


def record_mlb_request(endpoint: str, duration: float, error: Optional[BaseException] = None) -> None:
    """Record an MLB Stats API call. Only the endpoint template is labelled."""
    global _mlb_failures
    try:
        mlb_api_request_duration_seconds.labels(endpoint=endpoint).observe(duration)
        if error is None:
            mlb_api_requests_total.labels(endpoint=endpoint, outcome="success").inc()
            return
        mlb_api_requests_total.labels(endpoint=endpoint, outcome="failure").inc()
        mlb_api_request_failures_total.labels(
            endpoint=endpoint, error=type(error).__name__
        ).inc()
        with _lock:
            _mlb_failures += 1
    except Exception:  # pragma: no cover
        logger.debug("Failed to record MLB API metrics", exc_info=True)


def record_scheduler_status(running: bool) -> None:
    try:
        scheduler_up.set(1 if running else 0)
    except Exception:  # pragma: no cover
        logger.debug("Failed to record scheduler gauge", exc_info=True)


def record_schema_ready(ready: bool) -> None:
    try:
        schema_ready.set(1 if ready else 0)
    except Exception:  # pragma: no cover
        logger.debug("Failed to record schema gauge", exc_info=True)


def record_initial_sync_status(status: str) -> None:
    try:
        initial_sync_status.set(INITIAL_SYNC_STATES.get(status, 0))
    except Exception:  # pragma: no cover
        logger.debug("Failed to record initial sync gauge", exc_info=True)


def record_synced_counts(counts: Any) -> None:
    """Accept the dict returned by sync jobs and count rows per entity."""
    if not isinstance(counts, dict):
        return
    for entity, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            records_synced_total.labels(entity=str(entity)).inc(float(value))
            sync_last_entity_count.labels(entity=str(entity)).set(float(value))
            with _lock:
                _entity_counts[str(entity)] = int(value)
        except Exception:  # pragma: no cover
            logger.debug("Failed to record sync counts", exc_info=True)


class track_job:
    """Context manager recording start/end time, duration and outcome of a job."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._start = 0.0
        self._started_at = 0.0

    def __enter__(self) -> "track_job":
        self._start = time.perf_counter()
        self._started_at = time.time()
        try:
            sync_last_start_timestamp.labels(job=self.name).set(self._started_at)
            sync_in_progress.labels(job=self.name).set(1)
            with _lock:
                entry = _jobs.setdefault(self.name, {})
                entry.update({"running": True, "last_start_at": self._started_at})
        except Exception:  # pragma: no cover
            logger.debug("Failed to record job start", exc_info=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration = time.perf_counter() - self._start
        ended_at = time.time()
        outcome = "failure" if exc_type else "success"
        try:
            job_runs_total.labels(job=self.name, outcome=outcome).inc()
            job_duration_seconds.labels(job=self.name).observe(duration)
            sync_last_end_timestamp.labels(job=self.name).set(ended_at)
            sync_last_duration_seconds.labels(job=self.name).set(duration)
            sync_in_progress.labels(job=self.name).set(0)
            if outcome == "success":
                job_last_success_timestamp.labels(job=self.name).set(ended_at)
            with _lock:
                entry = _jobs.setdefault(self.name, {})
                entry.update(
                    {
                        "running": False,
                        "last_outcome": outcome,
                        "last_start_at": self._started_at,
                        "last_end_at": ended_at,
                        "last_run_at": ended_at,
                        "last_duration_seconds": round(duration, 3),
                        # message only — never a URL, DSN or credential
                        "last_error": type(exc).__name__ if exc else None,
                    }
                )
                if outcome == "success":
                    entry["last_success_at"] = ended_at
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
        status_classes = dict(_status_class_counts)
        jobs = {k: dict(v) for k, v in _jobs.items()}
        db_failures = _db_failures
        mlb_failures = _mlb_failures
        entity_counts = dict(_entity_counts)
        endpoints = {
            key: {
                "count": int(stat["count"]),
                "avg_latency_ms": round((stat["latency_sum"] / stat["count"]) * 1000, 2),
                "max_latency_ms": round(stat["max_latency"] * 1000, 2),
                "errors": int(stat["errors"]),
            }
            for key, stat in sorted(_route_stats.items(), key=lambda kv: -kv[1]["count"])
        }

    sync_jobs = {k: v for k, v in jobs.items() if "sync" in k or "refresh" in k or "ingest" in k}
    last_success = max(
        (v.get("last_success_at", 0) for v in sync_jobs.values()),
        default=0,
    )
    return {
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "requests_total": requests,
        "errors_total": errors,
        "error_rate": round(errors / requests, 4) if requests else 0.0,
        "avg_latency_ms": round((latency_sum / requests) * 1000, 2) if requests else 0.0,
        "status_counts": statuses,
        "status_classes": status_classes,
        "endpoints": endpoints,
        "top_routes": {k: v["count"] for k, v in list(endpoints.items())[:10]},
        "jobs": jobs,
        "database": {
            "up": bool(database_up._value.get()),  # type: ignore[attr-defined]
            "connection_failures": db_failures,
        },
        "mlb_api": {"request_failures": mlb_failures},
        "sync": {
            "last_success_at": last_success or None,
            "entity_counts": entity_counts,
            "jobs": sync_jobs,
        },
    }

