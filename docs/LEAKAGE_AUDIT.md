# Data integrity and leakage audit (Phase 3)

Audited 2026-11-02 against the code in this branch. Each item names where the guarantee
lives and how it is tested.

| Check | Status | Where enforced | Evidence |
| --- | --- | --- | --- |
| Target game excluded from its own features | pass | `backtest/engine.py` slices `games[:idx]` and asserts every prior date `< target`; `prediction/features.py` raises on any row `>=` cutoff | `tests/backtest/test_engine.py::test_target_and_future_games_do_not_leak`, `tests/prediction/test_leakage.py` (5 tests) |
| Future games excluded | pass | same slice; query path uses `before=` | same tests; altering a later game leaves earlier predictions byte-identical |
| Current-game actual minutes never used as projected minutes | pass | `MinutesAdjustedModel` reads only `FeatureSet.minutes_recent_mean` and `season_minutes_mean`, both from prior games; target minutes are stored only in `Observation.actual_minutes` for stratification | `test_backtest_prediction_equals_live_predict` (backtest output equals the live `predict()` with the same cutoff) |
| Current-game starter flag not used | pass | no feature reads `started`; the backtest's `prior_start_rate` uses only the previous 10 games' flags | code inspection; `FeatureSet` has no starter field |
| Roster / injury info not presented as pregame-known | pass by omission | no such data exists in the database; `FeatureSet.unavailable` lists them and every result carries `matchup_context_unavailable` | `tests/prediction/test_features.py::test_unavailable_features_listed` |
| Rolling windows chronologically correct | pass | `load_history` orders by `game_date, game_id`; features consume newest-first lists | `test_history_is_chronological_regardless_of_insertion_order`, `test_leakage.py::test_3` |
| Season changes handled consistently | pass | season mean uses the season containing the target date (fallback flagged); rolling windows may cross and are flagged; prior seasons count toward `min_prior_games` | `test_features.py::test_season_*`, `test_leakage.py::test_5` |
| Trades preserve identity | pass | one `person_id` → one player via `external_ids`; team recorded per game; `team_change_in_window` warning | `tests/test_sportsdataverse.py::test_traded_player_keeps_one_identity` |
| DNP / zero-minute policy explicit | pass | DNP rows not stored by default; stored zero-minute rows excluded from features and from backtest targets (`target_zero_minutes` exclusion) | `test_zero_minute_targets_are_excluded_and_counted`, `test_features.py::test_zero_minute_games_excluded` |
| Regular-season filtering consistent | pass | `season_types=("regular",)` default in query, prediction and backtest config | `tests/test_sportsdataverse.py::test_include_playoffs_and_dnp` |
| Synthetic lines pregame-only and model-independent | pass | `backtest/lines.py` uses only prior values; identical lines for every model | `test_synthetic_lines_are_half_point_pregame_and_model_free`; `obs.lines` equal across altered/unaltered databases |
| Calibration fitted on validation only | pass | `calibrate()` refuses identical fit/eval runs; CLI takes two run directories | `test_calibrators_fit_on_validation_only_apply_elsewhere`, `test_cli_calibrate_same_run_rejected` |
| Equal cohorts across models | pass | `build_cohorts` intersects observation ids over all model configurations; dropped counts reported | `test_equal_cohorts_determinism_and_sampling`, `test_write_read_analyse_and_report` |

## Known residual risks

* **Stat corrections.** NBA.com occasionally amends box scores after the game. We store
  the corrected value, which is also what a prop would settle on, but it means the
  "actual" could differ from what was displayed on game night. Immaterial for forecasting.
* **Selection on played games.** Only games the player actually played are targets.
  Pregame, a DNP is a possibility the model never faces. This matches prop settlement
  (voided picks) but means our probabilities are conditional on playing.
* **Schedule knowledge.** Rest days use the previous game date, which is pregame-known.
  We do not yet use the published schedule to know a *future* back-to-back.
* **Data freshness ≠ real-time.** Replaying history is not prospective validation. The
  2026-27 season should be used for genuinely prospective, timestamped predictions.

## ESPN cross-check follow-up (the 12 unmatched rows)

The Phase 2.5 sample matched 288 of 300 rows by exact name. Re-running the check with
accent-stripped names resolved all 12 (Jokić, Valančiūnas, Nurkić, Bogdanović, Matković,
Diabaté): 300 of 300 identical on points, rebounds and assists. No shared stable player
ID exists between the two sources (NBA `person_id` vs ESPN `athlete_id`), so name plus
date remains the join key; a team-plus-date-plus-stat-line fallback was also implemented
for the check and was not needed.

Extending the comparison to every stored 2024-25 regular-season row:

| Metric | Value |
| --- | --- |
| Stored rows | 26,306 |
| Matched to ESPN by normalised name and date | 26,301 |
| Identical pts/reb/ast | 26,299 |
| Discrepancies | 2 (same OKC game, 2025-03-02: ESPN credits Jalen Williams with one assist that NBA.com credits to Isaiah Hartenstein) |
| Unmatched | 5 (name-format differences beyond accents) |

The two discrepancies are a scorer attribution difference between providers, not a
loading error. No stored values were modified.
