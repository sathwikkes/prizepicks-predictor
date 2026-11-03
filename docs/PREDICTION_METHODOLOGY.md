# Prediction methodology (Phase 2 baselines)

Status: implemented, backtested on three real seasons (`docs/BACKTEST_RESULTS.md`) and
calibrated on synthetic evaluation lines (`docs/CALIBRATION.md`). `calibration_status` on
each result states which artifact, if any, was applied and how it was validated.

## 1. History

`get_player_game_logs(player, before=as_of_date)` returns every stored game strictly before
the target date, newest first. Games with zero minutes are excluded from all averages and
counted as `dnp_games`. `build_features` raises if any row is on or after the cutoff.

## 2. Features (`FeatureSet`)

| Group | Features |
| --- | --- |
| Season | games, mean, sample SD, mean minutes for the season containing `as_of_date`; previous season used (and flagged) if the current one has no games |
| Rolling | last-5 mean, last-10 mean, last-10 SD, EWMA (half-life 5 games over all history) |
| Per-minute | season rate, last-10 rate (`sum(stat) / sum(minutes)`) |
| Minutes | last-5 mean, last-10 SD; `minutes_volatile` warning when SD / mean > 0.30 |
| Context | home/away means, rest days, back-to-back, latest team, teams in last 10 |
| Unavailable | opponent defensive rating, pace, injuries, lineup status, usage, teammate availability |

Rolling windows may span a season boundary or a trade; both raise warnings rather than
silently truncating, because the right policy is an empirical question for Phase 3.

## 3. Projection models

| Name | Projection | Key assumption |
| --- | --- | --- |
| `season_average` | season mean | all games in the season equally informative |
| `rolling_5`, `rolling_10` | mean of last N games | recent form dominates |
| `ewma` (default) | EWMA, half-life 5 games | geometric decay of relevance |
| `minutes_adjusted` | last-10 per-minute rate × (0.6 × last-5 minutes + 0.4 × season minutes) | production linear in minutes; 60/40 blend is a prior |

None adjusts for opponent or rest. All require at least 3 games with minutes; fewer raises
`InsufficientDataError` instead of guessing. Under 10 games adds a `small_sample` warning.
The default model is a placeholder choice; Phase 3 decides on evidence.

## 4. Variance

Outcome variance is the sample variance of the stat over the last 20 games with minutes,
multiplied by `(1 + 1/n)` to account for uncertainty in the estimated mean (the predictive
variance of a new draw under a normal-mean model), floored at 0.75² so a flat history
cannot produce near-certainty. Minutes variability is already embedded in observed stat
variance and is not added a second time.

## 5. Predictive distribution

The projection becomes the mean of a distribution on non-negative integers.

| Family | When | Parameterisation |
| --- | --- | --- |
| Negative Binomial | `auto` when variance > 1.05 × mean | `r = μ² / (σ² − μ)`, `p = r / (r + μ)`, `r` capped at 1000 |
| Poisson | `auto` otherwise, or NB fallback when σ² ≤ μ | `λ = μ` |
| Normal | benchmark only | `N(μ, σ²)` with ±0.5 continuity correction, truncated at −0.5 so the integer pmf sums to 1 |
| Empirical | opt-in, ≥ 15 games | relative frequencies of past values; ignores the projection, falls back to `auto` otherwise |

NBA points are typically overdispersed, so `auto` usually selects Negative Binomial for
points and may select Poisson for low-count rebounds or assists. This rule is a sensible
default, not an empirically selected one.

## 6. More / Less / Push

For integer-valued stats and line `L`:

* Integer `L`: `P(push) = P(X = L)`, `P(less) = P(X ≤ L − 1)`, `P(more) = 1 − P(less) − P(push)`.
* Half-point `L`: `P(push) = 0`, `P(less) = P(X ≤ ⌊L⌋)`, `P(more) = 1 − P(less)`.

The three probabilities are validated to be in `[0, 1]` and to sum to 1 (`OutcomeProbabilities`).
The 80% interval is the 10th and 90th percentiles of the same distribution.

## 6b. Calibration (Phase 3.5)

After the raw triple, `predict()` looks up a calibration artifact for the exact stat,
model, version and family. If found, the conditional P(more | no push) is mapped through
the fitted isotonic function and the non-push mass is re-split; the push probability is
unchanged. Raw and calibrated triples are both returned. See `docs/CALIBRATION.md`.

## 7. Known limitations requiring real data

1. Distribution family selection and the 1.05 overdispersion threshold are untested.
2. EWMA half-life, rolling windows, the 20-game variance window and the 60/40 minutes
   blend are priors, not fitted values.
3. Variance is treated as stationary; role changes or injuries that change variance are
   not modelled.
4. No opponent, pace, rest, home/away or injury adjustment is applied, even though
   home/away and rest features are computed.
5. Rolling windows across trades and season boundaries are allowed and only flagged.
6. Lines are compared to a player's own distribution only; market information is unused.
7. Calibration is unknown. Reported probabilities may be systematically over- or
   under-confident, and no profitability inference is valid.

## 8. Phase 3 evaluation plan

1. **Data**: load several seasons of real per-game logs through the provider adapter
   (BALLDONTLIE paid tier or a licence-compatible historical dataset via the CSV adapter).
2. **Protocol**: walk-forward by date. For each (player, game, stat) with ≥ 10 prior games,
   predict using only prior data, then score against the actual value.
3. **Point metrics**: MAE and RMSE per model and stat, versus the season-average baseline.
4. **Probabilistic metrics**: for synthetic lines at the predicted median and at ±2 of it,
   Brier score, log loss and reliability curves of P(more) in deciles. Compare families.
5. **Selection**: choose default model, distribution rule and window parameters on a
   validation season; confirm on a later held-out season.
6. **Wagering simulation**: only if timestamped historical PrizePicks lines are obtained;
   otherwise report forecasting skill only and say so.
