# Architecture notes

## Layers

```
CLI / (future) FastAPI
        │
   backtest/           walk-forward engine, metrics, calibration, reports (reads prediction/)
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
| `services/data_quality.py` | coverage, integrity and provenance report | db |
| `backtest/engine.py` | walk-forward loop over eligible player-games; shares one feature set per observation across models | prediction, history |
| `backtest/lines.py` | synthetic pregame-only, model-independent half-point lines | nothing |
| `backtest/metrics.py`, `calibration.py` | pure numpy metrics, bootstrap CIs, Platt and isotonic calibrators | numpy, scipy |
| `backtest/report.py`, `calibrate.py` | persistence (`run.json`, gzip CSV), equal-cohort analysis, charts | matplotlib |
| `backtest/fit_calibration.py` | writes versioned `CalibrationArtifact`s from runs | backtest, prediction |
| `prediction/calibrators.py` | artifact schema, registry, push-preserving application | schemas |
| `services/forecast.py` | schedule-driven pregame forecasts into immutable snapshots | prediction, db |
| `services/evaluation.py` | post-game outcomes, DNP/void/missing policy | db |
| `services/monitoring.py` | prospective performance report with cluster bootstrap | backtest.metrics |
| `ingestion/schedules.py` | ESPN schedule adapter into `scheduled_games` | ingestion |

**The backtest reuses the engine's pure functions, not a copy.** `build_features`,
`model.project`, `predictive_variance`, `resolve_family` and the family parameterisation
are the same code the live `predict()` calls; `pmf_array` is a vectorised evaluation of
the same distribution and is tested equal to `Predictive.pmf`. A test asserts a backtest
record equals a live prediction made with the same cutoff.

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

**Season type lives on the game.** `games.season_type` (regular, playoffs, playin,
preseason, allstar, other) lets one database hold every game type while
`get_player_game_logs` defaults to regular season, which is what full-game props are priced
on. The prediction engine inherits that default. `init_db` adds the column to databases
created before it existed.

**Two-file provider joins happen inside the adapter.** The SportsDataverse adapter joins
NBA.com player box scores to team game logs on `(game_id, team_id)` before emitting
`NormalizedGameLog`, so the loader never learns about provider file layouts. Downloads are
cached under `data/raw/` and re-used; schema drift (a missing column) is a `ProviderError`,
not a silent miscount.

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

**Snapshots are immutable at the ORM level.** A `before_update` listener on
`PredictionSnapshot` raises, so no code path can quietly revise a pregame forecast; the
only way to record new information is a new revision or an outcome row.

**Calibration is a separate, versioned artifact.** The model never changes when a
calibrator is refitted, and a prediction records which artifact touched it, so any
snapshot can be reproduced from its model version plus artifact id.

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
