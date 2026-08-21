# Read-only inspection: POST /admin/build-snapshots

No endpoint executed, no code changed, no writes performed. All findings come from reading `admin.py`, `training_dataset.py`, `feature_builder.py`, `session.py`, plus the read-only `GET /admin/statcast-audit`.

## 1. Parameters and defaults

| Param | Type | Required | Default |
|---|---|---|---|
| `start` | date | yes | none |
| `end` | date | yes | none |
| `limit` | int (>=1) | no | `None` (no cap) |
| `window_days` | int 1-365 | no | `30` |

`start > end` returns HTTP 400. Runs inside `track_job("manual_build_snapshots")` and reports counts via `record_synced_counts`.

## 2. Date range when start/end omitted

There is none — both are `Query(...)` (required). Omitting either returns HTTP 422. So no accidental full-history run is possible.

## 3. 2024 vs 2026 by default

Neither. The range is explicit and required; the builder only enumerates pairs inside `[start, end]`.

## 4. Expected batter-game pairs

Pairs come from `DISTINCT (batter_id, game_id, game_date)` in `statcast_pitches`. From the live audit (511,639 pitches; 1,779 games):

- 2024 (1,630 games, 120,336 PA results): roughly 29,000-33,000 pairs
- 2026 (149 games, 11,268 PA results): roughly 2,700-3,100 pairs
- Combined: roughly 32,000-36,000 pairs

These are estimates derived from PA counts and games; exact counts would require running the enumerating query.

## 5. Are existing snapshots skipped?

No — `upsert_snapshot` looks up `(batter_id, game_id, feature_set_version)` and updates in place when present, inserts otherwise. Re-running is idempotent (no duplicates) but not cheaper: every pair is recomputed.

## 6. Resumable?

Effectively yes, by re-running narrower date ranges: idempotent upserts mean overlap is harmless. Note it is not resumable *within* a single failed call, because of item 7.

## 7. Batching vs one transaction

One transaction. `_build_snapshots_sync` opens a single `session_scope()`; the loop only `flush()`es, and the commit happens once when the context manager exits. A crash mid-run rolls back everything. Per-pair exceptions are caught and counted as `skipped`, so bad pairs do not abort the run.

## 8. Expected database impact

- Reads: about 5-6 aggregate queries per pair against `statcast_pitches` (indexed on batter/pitcher + game_date), plus small `players`/`games`/`weather`/`lineups` lookups.
- Writes: one `feature_snapshots` row per pair, roughly 40 numeric columns — a full 2024+2026 run is about 33k rows, on the order of tens of MB including indexes.
- Risk on a full run: a long-lived single transaction plus tens of thousands of aggregate queries; likely many minutes and possible request/proxy timeout on Railway, with a rollback of all work.

## 9. Is limit=100 safe?

Yes. `limit` caps the pair list before any work, and pairs are ordered by `game_date, game_id, batter_id`, so a small limit does a deterministic, tiny, fast-committing run. This is the recommended first production sample.

## 10. feature_builder / leak safety

Confirmed. `build_snapshots` calls `feature_builder.build_features(..., include_label=True)`; `historical_bounds` sets `end = target_game_date - 1 day` and `start = target_game_date - window_days`, and every Statcast aggregate is bounded by that window. The label is the only thing read from the target game (`label_for_target_game`, filtered by `game_id`). Bounds are enforced inside the builder — callers cannot opt out.

## 11. Statcast ingestion

None. `training_dataset.py` and `feature_builder.py` only `SELECT` from `statcast_pitches`. No import of `statcast_service`.

## 12. External API calls

None. Weather and lineups are read from stored rows (`GameWeather`, `game_lineups`); park factors come from the existing DB-backed service. No HTTP client is touched.

## 13. Model training

None. Training lives behind the separate `POST /admin/train-model`; `build-snapshots` never calls `train_model` or `save_artifact`.

## Safest first production sample (recommended)

```
POST /admin/build-snapshots?start=2024-04-01&end=2024-04-02&limit=100&window_days=30
```

Then verify with `GET /admin/model-status` (returns `corpus` rows/positives/base_rate) before widening the range in month-sized slices.
