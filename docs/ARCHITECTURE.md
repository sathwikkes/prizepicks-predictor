# Architecture notes

## Layers

```
CLI / (future) FastAPI
        │
   prediction/         features -> models -> distributions -> predict()
        │
   services/           business logic; returns Pydantic read models
        │
   ingestion/          provider adapters -> NormalizedGameLog -> loader
        │
   db/                 SQLAlchemy ORM, engine/session helpers
```

### Prediction package

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `prediction/schemas.py` | `PredictionRequest`, `FeatureSet`, `PredictionResult`, enums | nothing internal |
| `prediction/features.py` | pure functions from `GameLogRow` history to `FeatureSet` | schemas |
| `prediction/models.py` | four baseline `ProjectionModel`s and the registry | schemas |
| `prediction/distributions.py` | Poisson / NegBin / Normal / Empirical, More/Less/Push | schemas, scipy |
| `prediction/service.py` | orchestration, error mapping, warnings | all of the above, services |

Adding a model means implementing `project(FeatureSet) -> ModelOutput` and registering it;
nothing else changes. The same holds for a new distribution family.

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

**Point-in-time guarantee, enforced twice.** `get_player_game_logs(..., before=date)`
excludes games on or after a date at the query layer, and `build_features` raises if any
row it receives is not strictly before the cutoff. `tests/prediction/test_leakage.py`
proves the target game, future games, insertion order and future seasons cannot alter a
prediction.

**Models return projections; distributions return probabilities.** A projected mean is
never converted to a probability by a heuristic. The projection becomes the mean of an
explicit predictive distribution whose variance comes from the player's own recent
outcomes, and the probability of More/Less/Push is integrated from that distribution.
See `docs/PREDICTION_METHODOLOGY.md`.

**Missing context is reported, not imputed.** `FeatureSet.unavailable` lists the
matchup, pace, injury, lineup and usage features the engine is designed to accept but
cannot compute from current data; every result carries a `matchup_context_unavailable`
warning until a source exists.

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
