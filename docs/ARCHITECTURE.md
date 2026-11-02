# Architecture notes (Phase 1)

## Layers

```
CLI / (future) FastAPI
        │
   services/           business logic; returns Pydantic read models
        │
   ingestion/          provider adapters -> NormalizedGameLog -> loader
        │
   db/                 SQLAlchemy ORM, engine/session helpers
```

Dependencies point downward only. `schemas/` is shared by every layer and depends on
nothing internal. `config.py` is read at the edge (CLI) and passed in, so library code is
never coupled to environment variables.

## Decisions

**Provider adapters yield a normalized record.** Every source converts to
`NormalizedGameLog` before touching the database. Validation (ranges, team != opponent,
season inference) lives in one place, and the loader is provider-agnostic.

**Bad rows are yielded, not raised.** A provider yields `RecordValidationError` for a
malformed record and raises `ProviderError` only for failures that invalidate the whole
run (missing file, auth, network). The loader counts rejections and stores them on the
ingestion run instead of aborting a large import for one bad line.

**Surrogate keys plus `external_ids`.** Internal IDs are integers. Provider IDs are
stored in `external_ids(source, entity_type, external_id)`, so the same player can be
linked from several providers later without schema changes. Cross-source identity
resolution (deciding that `csv:FIX-001` and `balldontlie:777` are the same person) is
deliberately deferred.

**Natural uniqueness constraints at the database level.** `games(date, home, away)` and
`player_game_stats(game, player)` are unique in the schema, not only in application
logic. The loader resolves on those keys so re-imports are idempotent and report
inserted / updated / unchanged honestly.

**Point-in-time hook from day one.** `get_player_game_logs(..., before=date)` excludes
games on or after a date. Phase 2 features will be computed through this path so a
prediction for a game can never see that game's own box score.

**Minutes stored as decimal float.** `34:30` becomes `34.5`; per-minute rate features
need arithmetic, not strings.

**SQLite + SQLAlchemy.** Zero-ops for the MVP; the ORM keeps a move to Postgres a URL
change. Foreign keys are enabled via PRAGMA on every connection.

**Migrations.** `init_db` uses `create_all`, which is sufficient while the schema is
young and the database is disposable. Alembic will be introduced before the first
schema change that must preserve data (expected when projections/predictions tables land).

**No Polars/Pandas yet.** Phase 1 only moves rows; dataframes arrive with feature
engineering in Phase 2.

## Planned tables (not yet implemented)

* `projections` — PrizePicks (or other) lines: player, stat type, line, source, captured_at.
* `model_versions` — name, version, training window, feature list, metrics.
* `prediction_snapshots` — predicted mean/distribution, P(more), P(less), model version,
  predicted_at, feature hash.
* `prediction_outcomes` — realized stat, hit/miss/push, joined to the snapshot.
