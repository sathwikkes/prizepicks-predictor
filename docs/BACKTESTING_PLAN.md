# Phase 3 plan: walk-forward evaluation on real NBA data

Three complete real seasons plus a partial one are now in the database. This is the plan
for turning them into evidence about the Phase 2 baselines. Nothing here is implemented yet.

## 1. Observation set

One observation = (player, game, stat) for regular-season games where the player had at
least 10 prior games with minutes before that game date. Points, rebounds and assists each
form their own set. Expected size: roughly 70,000 observations per stat.

## 2. Chronology

| Role | Season | Use |
| --- | --- | --- |
| Development | 2023-24 | pick windows, half-life, variance window, distribution rule |
| Validation | 2024-25 | fit any calibration map (isotonic / Platt) on this season only |
| Test | 2025-26 | report final metrics once; never tune on it |
| Live | 2026-27 | prospective tracking as the season unfolds |

Within a season, iterate game dates in order. For each date, every prediction uses
`get_player_game_logs(before=date)` through the existing `predict()` path, so the
target game and later games are structurally invisible (the leakage tests already prove
this). Prior seasons are allowed as history.

## 3. Models and distributions compared

All five registered models (`season_average`, `rolling_5`, `rolling_10`, `ewma`,
`minutes_adjusted`) and all four distribution families, on the identical observation set.
A model that cannot predict an observation (insufficient data) is scored as missing and
every model is compared on the intersection, so no model gets credit for abstaining.

## 4. Metrics

Point forecast: MAE and RMSE versus the realised stat, per stat type, per model, with the
season-average model as the reference and bootstrap confidence intervals on the differences.

Probabilistic: because real PrizePicks lines are unavailable, synthetic lines are generated
from the prediction itself at the predicted median and at median ±1, ±2, ±3 (half-point lines
only, so pushes do not complicate the comparison). For each line: Brier score, log loss, and
reliability curves (predicted P(more) in deciles vs observed frequency). Also the PIT
histogram and the empirical coverage of the 80% interval, which test the whole distribution
rather than one threshold.

## 5. Calibration

Fit isotonic regression and Platt scaling on 2024-25 predictions, apply to 2025-26, and
report whether they improve Brier and log loss out of sample. Only then may a result be
called "calibrated", and only for the stat and line range it was measured on.

## 6. Wagering simulation: separated and conditional

No historical PrizePicks lines exist in the repository. A profit simulation on synthetic
lines would be circular and is not done. If timestamped pregame lines are obtained later,
a separate simulation will score picks only against lines that existed before tip-off and
will report hit rate and ROI with uncertainty intervals and the payout rules used.

## 7. Outputs

A `backtest` CLI command writing per-observation predictions to a Parquet/CSV file plus a
summary table in `docs/backtests/<model_version>.md`, both reproducible from a fixed
database snapshot. Results feed the `model_versions` table planned since Phase 1.

## 8. Data still worth adding before Phase 3 conclusions

* `nba_stats_schedules` for dates of play-in games and for rest/travel features.
* `nba_stats_rosters` or injury reports with as-of timestamps for availability features.
* Team box scores for opponent pace and defensive context.
