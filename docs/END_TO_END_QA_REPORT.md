# End-to-end QA report (Phase 4A.1 stabilization)

Audit date: 2026-11-03 (05:58–06:30 UTC). Branch `feature/phase-4a-1-stabilization`.
Database: the real local SQLite database built in Phases 2.5–3.5 (not committed).

**Readiness: working.** Every page renders against real data, dashboard and CLI outputs
equal the service for identical inputs, projections and probabilities were reproduced from
raw rows with independent formulas, and the prospective workflow passed its checks. No P0
or P1 defect remains open. Limits are listed at the end.

## 1. Testing matrix

| Feature | Intended behaviour | Automated tests | Real-data verification |
| --- | --- | --- | --- |
| Box-score ingestion (SportsDataverse) | idempotent upsert, DNP excluded, provenance | `tests/test_sportsdataverse.py` (15) | `ppp ingest-sdv --season 2027` re-run: inserted 0, updated 0, unchanged 2,079 |
| Schedule ingestion (ESPN) | upsert by game id, UTC tip-offs, statuses | `tests/forecasting/test_schedule_and_cli.py` | re-run: inserted 0, updated 0, unchanged 1,200 |
| Database integrity | no duplicates, FKs enforced, one identity per player | `tests/test_db.py`, `tests/test_ingestion.py` | `PRAGMA integrity_check` ok; 0 duplicate player-games, games, external ids; 0 FK violations; 0 players with >1 id or name |
| Game-log query | regular season default, `before` cutoff, accent-insensitive search | `tests/test_query.py` | history for a 2026-11-04 target ends 2026-10-30; `jokic` resolves `Nikola Jokić` |
| Features | computed strictly before the target | `tests/prediction/test_features.py` (17), `test_leakage.py` (5) | independent recomputation of season/rolling/EWMA/minutes-adjusted projections matched to 1e-9 |
| Distributions | Poisson / NB / Normal / Empirical; probabilities valid; push at integer lines | `tests/prediction/test_distributions.py` (~40) | scipy recomputation of P(more), P(less), P(push), SD, 80% interval matched to 1e-9; 300 model×family×stat×line combinations valid |
| Calibration | exact artifact match; conditional map; push preserved; fallback | `tests/forecasting/test_calibrators.py` (9) | calibrated values re-derived from the artifact JSON; applied only to ewma/auto; integer-line warning present; artifact reproduces Phase 3 held-out ECE 0.0018 |
| `predict()` service | errors for missing history / player / line; metadata | `tests/prediction/test_service.py` | CLI, service and dashboard identical (23.12; 0.276 raw; 0.308 calibrated) |
| Backtest harness | chronological, equal cohorts, deterministic | `tests/backtest/*` (31) | Phase 3 artifacts untouched; dashboard table equals `comparison.csv` |
| Forecast generation | tip-off guard, pre-target history, idempotent, revisions | `tests/forecasting/test_forecast_and_evaluation.py` (16) | 1,191 rows = 397 players × 3 stats × 14 games, 1 batch, 0 revisions, 0 rows with cutoff ≥ tip-off; 2026-11-02 refused (15 started) |
| Snapshot immutability | ORM refuses updates; **new:** DB triggers refuse UPDATE/DELETE | `test_snapshot_contents_and_immutability`, **new** `test_raw_sql_cannot_mutate_or_delete_snapshots` | ORM update refused on the real DB |
| Outcome evaluation | evaluated / did_not_play / voided / not_scheduled / missing_result; pushes excluded | 7 tests incl. `not_scheduled` | 24 replay outcomes equal box-score rows by player and date; errors and More/Less flags correct; 12 `missing_result` (no 2025-26 schedule loaded) |
| Monitoring | counts, metrics, cluster bootstrap, preliminary flag | `tests/forecasting/*`, `tests/dashboard/*` | report consistent with outcomes table |
| Dashboard data layer | search, profile, logs, prediction view, snapshots, status, backtests | `tests/dashboard/test_dashboard_data.py` (9) | season table equals SQL aggregates (70 games, 29.6 pts in 2024-25) |
| Dashboard pages | render; inputs change outputs; error states | `tests/dashboard/test_app_smoke.py` (5), **new** `test_stabilization.py` (8) | all five pages rendered on the real DB via AppTest; see §4 |

## 2. Commands and results (final run)

```
uv run pytest                 273 passed, 0 failed
uv run ruff check .           All checks passed
uv run ruff format --check .  all files formatted
uv run mypy                   Success: no issues found in 45 source files
```

Real-data audit script (`scratchpad/audit.py`, not committed): 44 checks, 1 failure before
fixes (performance page default run), 0 after.

## 3. User journeys verified (real data unless noted)

1. **Player research**: search `jokic` → Nikola Jokić, 219 games, four seasons; 2024-25 row
   shows 70 games / 29.6 points, equal to a direct SQL aggregate; four charts render;
   `nobody` shows the empty-result message.
2. **Prediction generation**: Jokić, points, 27.5, 2026-11-04 → projection 23.12, raw 0.276,
   calibrated 0.308, artifact id displayed; same for rebounds 11.5 and assists 9.5.
   Service, CLI and dashboard agree to displayed precision.
3. **Changing inputs**: switching to rebounds at 24.0 replaced every value (push 0.045
   appears, points values gone) and raised the integer-line calibration warning.
4. **Historical forecast**: a prediction for Jokić's 2026-04-12 game used history ending
   2026-04-08; the actual 23 points that night is not an input.
5. **Prospective forecast**: the 2026-11-04 batch (1,191 snapshots, cutoff 05:15 UTC on
   2026-11-03, earliest tip-off 00:00 UTC on 2026-11-05) is intact; re-running `forecast
   --save` inserted nothing; forecasts for 2026-11-02 were refused as already started.
   No snapshot was modified during the audit.
6. **Outcome evaluation**: the March replay's 24 evaluated outcomes match box scores
   player-by-player; 12 are `missing_result` because no 2025-26 schedule is loaded, so
   "no game" cannot be told from "not loaded" (documented).
7. **Error handling** (fixtures): invalid threshold 24.3 → "multiple of 0.5" message;
   unknown player → not-found message; insufficient history → warning; missing
   calibration directory → uncalibrated with reason; empty database → every page renders
   with guidance; unsupported model → CLI rejects, service raises `UnknownModelError`;
   unreachable database path → "Database unavailable" error state (new).

## 4. Dashboard verification detail

| Page | Checks passed on the real database |
| --- | --- |
| Overview | latest box score 2026-11-01; next game date 2026-11-04 (14 games); coverage table equals the seasons query; 3 artifacts listed; no warnings (data current) |
| Players | accent-insensitive search; 219 games; season averages equal SQL; 4 charts; season and game-count filters tested with fixtures |
| Prediction | projection, raw and calibrated P(more), push and integer-line warning equal the service; distribution chart pmf sums to 1 and its mass above the line equals P(more) |
| Forecasts | caption "1191 rows = 1191 unique player-game-stat combinations (397 players, 14 games, 3 stats, 1 batch, 0 revisions); 1191 saved before a known tip-off, 0 without"; filters tested with fixtures |
| Performance | ewma points MAE shown equals `docs/backtests/test_2025-26/comparison.csv` (4.6426) once the default run was corrected; prospective tab separate |

Browser automation was not used; all interface checks ran through `streamlit.testing.v1.AppTest`
plus a headless server health check (`/_stcore/health` ok, root HTTP 200). Visual layout
was not verified.

## 5. Bugs found and fixed

| Sev | Finding | Fix |
| --- | --- | --- |
| P1 | Performance page defaulted to the last run in the list, which on a machine with a local smoke run is the 500-game smoke run, so the first table shown did not match the reported held-out results | default to the held-out test run; warn when a non-partition run is selected |
| P1 | Database connection failure rendered a raw traceback | `guarded()` wrapper on every page shows an error state with the configured URL |
| P2 | Snapshot status filter applied after the row limit, so a rare status could be missed | filter in SQL before the limit |
| P2 | Relative `PPP_CALIBRATION_DIR` depended on the working directory; launching from elsewhere silently disabled calibration | resolve against the repository root when the relative path is absent |
| P2 | Forecast table datetime columns unlabelled as UTC; pandas rounding warning on datetime columns | columns renamed `cutoff_utc`/`tipoff_utc` with a caption; round numeric columns only |
| P2 | Rolling-mean legend implied a full history when only loaded games are averaged | legend says "within loaded games" |
| Hardening | immutability relied on an ORM hook | SQLite `BEFORE UPDATE/DELETE` triggers on `prediction_snapshots`, installed idempotently by `init_db` |

No P0 defects were found. No statistical result was wrong.

## 6. Remaining limitations

- **Delayed box scores**: the current season lags live games by about a day; prospective
  outcomes for 2026-11-04 cannot be evaluated until the release updates.
- **Participants are expected, not confirmed**; inactive players evaluate as did-not-play.
- **No schedule for past seasons** is loaded, so replay rows for off days are
  `missing_result` rather than `not_scheduled`. Loading `ppp ingest-schedule --season 2026`
  would resolve them.
- **Integer-line calibration** is an extrapolation from half-point fits (warned, not fixed).
- **Local time display**: all timestamps are shown in UTC; no local-time conversion.
- **Licensing**: non-commercial research only; see `docs/LICENSING_RISK.md`.

## 7. Untested

- Visual rendering in a browser (layout, responsiveness).
- Live evaluation of the 2026-11-04 prospective batch (data not yet published).
- Postponed/canceled games on the real schedule (none occurred; fixture-tested only).
- Non-SQLite databases.

## 8. Data-source dependencies

SportsDataverse GitHub releases: `nba_stats_player_boxscores` and `nba_stats_player_game_logs`
(team logs) for box scores, `espn_nba_schedules` for the schedule. Both refreshed daily by
SportsDataverse; no API keys. Calibration artifacts and backtest summaries are committed.
