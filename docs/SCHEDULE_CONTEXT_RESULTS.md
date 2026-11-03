# Phase 4B: schedule-aware forecasting — result

Date: 2026-11-03. Verdict: **the schedule-adjusted model does not improve on EWMA. EWMA
stays the production default.** The features are kept because they are correct, cheap and
pregame-safe; the adjustment model `ewma_schedule` is available for research only.

## What was built

- `prediction/schedule_context.py`: team-based rest days, back-to-back (rest ≤ 1 day),
  three-in-four (two team games on the three days before the target), games in the last 7
  days, venue. Dates come from completed games plus loaded schedule rows strictly before
  the target; any date on or after the target raises.
- `FeatureSet.schedule` is populated by `predict()` (via `services/team_schedule.py`) and by
  the backtest engine (from the games table). Historical coverage is complete: every team
  has all 82 regular-season games in 2023-24, 2024-25 and 2025-26 (1,343 team
  back-to-backs), so no extra schedule download was needed.
- `ewma_schedule` = EWMA × rest-bucket factor × venue factor. Factors are per-stat ratios
  of actual to EWMA projection within each bucket, normalised to normal rest, fitted on
  the development season only (`ppp fit-schedule-factors`), stored in
  `artifacts/schedule_factors.json` with counts and provenance.

## Protocol

| Role | Season | Use |
| --- | --- | --- |
| Development | 2023-24 | fit the factors (21,032 player-games; buckets: 3,667 back-to-back, 2,866 three-in-four, 4,051 long rest, 10,448 normal) |
| Validation | 2024-25 | compare `ewma` vs `ewma_schedule` on identical observations |
| 2025-26 | not used | already inspected in Phase 3; not re-used as "independent" evidence |
| 2026-27 | untouched | prospective monitoring remains the genuinely unseen test |

## Fitted factors (2023-24)

| stat | back-to-back | three-in-four | long rest | home | away |
| --- | --- | --- | --- | --- | --- |
| points | 1.025 | 1.004 | 0.984 | 1.007 | 0.993 |
| rebounds | 1.022 | 1.014 | 1.008 | 1.006 | 0.994 |
| assists | 1.011 | 0.988 | 0.984 | 1.018 | 0.982 |

The development season says players *exceeded* their EWMA on back-to-backs. That is a
selection effect, not a performance effect: who plays the second night of a back-to-back is
decided by availability, and EWMA already reflects recent form. It did not hold a year later.

## Validation (2024-25, n = 25,158 player-games per stat, `auto` family)

| stat | model | MAE | RMSE | bias | Brier | ECE | 80% cov |
| --- | --- | --- | --- | --- | --- | --- | --- |
| points | ewma | 4.683 | 6.127 | −0.100 | 0.2337 | 0.0306 | 0.798 |
| points | ewma_schedule | 4.685 | 6.129 | −0.081 | 0.2336 | 0.0293 | 0.797 |
| rebounds | ewma | 1.947 | 2.566 | −0.028 | 0.2011 | 0.0161 | 0.860 |
| rebounds | ewma_schedule | 1.951 | 2.567 | +0.003 | 0.2010 | 0.0139 | 0.860 |
| assists | ewma | 1.364 | 1.851 | −0.011 | 0.1848 | 0.0208 | 0.895 |
| assists | ewma_schedule | 1.363 | 1.850 | −0.018 | 0.1848 | 0.0221 | 0.894 |

Paired bootstrap, `ewma_schedule` minus `ewma` (negative = better), 95% CI:

| stat | MAE, all games | MAE, back-to-backs only (n = 4,566) | Brier, all |
| --- | --- | --- | --- |
| points | +0.0021 [−0.0000, +0.0045] | **+0.0214 [+0.0115, +0.0314]** worse | −0.00007 [−0.00020, +0.00006] |
| rebounds | **+0.0033 [+0.0025, +0.0041]** worse | **+0.0096 [+0.0062, +0.0128]** worse | −0.00005 [−0.00013, +0.00002] |
| assists | **−0.0010 [−0.0019, −0.0003]** better | **+0.0025 [+0.0007, +0.0042]** worse | +0.00003 [−0.00007, +0.00012] |

Back-to-backs vs ordinary rest, EWMA on 2024-25 (unadjusted): points MAE 4.730 vs 4.628,
bias −0.30 vs −0.09; rebounds bias −0.12 vs 0.00; assists bias −0.03 vs −0.01. So players
*under*-perform EWMA on back-to-backs in 2024-25 by roughly 1%, the opposite sign of the
2023-24 fit. A factor that must flip sign between consecutive seasons is noise.

## Conclusion

- No meaningful improvement in MAE, RMSE, Brier, calibration or coverage on any stat. The
  one statistically detectable overall gain (assists, −0.001 MAE) is negligible, and the
  adjusted model is reliably worse on the very games it targets.
- **Recommendation: keep `ewma` as the default.** Do not add `ewma_schedule` to the
  dashboard's default path. It remains selectable via `ppp predict --model ewma_schedule`
  and in backtests for future research.
- The useful outcome is infrastructure: rest and venue context now flow into every
  prediction and every backtest observation, the report has a `rest` stratum, and the
  fitting/evaluation loop is reproducible in under three minutes. A rest effect, if it
  exists, is small enough that it will need a within-player design (opponent, minutes
  availability, travel) rather than a global multiplier.

## Reproduce

```bash
uv run ppp backtest --season 2023-24 --models ewma --families auto --no-empirical --no-charts --run-name sched_dev_2023-24
uv run ppp fit-schedule-factors --run data/backtests/sched_dev_2023-24 --out artifacts/schedule_factors.json
uv run ppp backtest --season 2024-25 --models ewma,ewma_schedule --families auto --no-empirical --no-charts --run-name sched_val_2024-25
```
