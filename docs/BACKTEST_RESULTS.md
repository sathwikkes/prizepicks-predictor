# Backtest results: baseline models on real NBA games (Phase 3)

Generated 2026-11-02 from three full-season walk-forward runs (`ppp backtest`) and
validation-fitted calibration (`ppp calibrate`). Full tables, reliability bins, strata and
charts for each run are under `docs/backtests/<run>/`; the raw per-prediction records stay
in the local, gitignored `data/backtests/` and can regenerate every table without re-running.

**Read this first.** Lines are *synthetic evaluation thresholds*: floor(trailing-10-game
median) + 0.5 at offsets −2 … +2, built from pregame history only and identical for every
model. They are not PrizePicks lines. No hit rate, ROI or betting edge is claimed or
computable from these results.

## Protocol

| Role | Season | Player-games | Observations (×3 stats) | Excluded: <10 prior played games | Excluded: 0-minute target |
| --- | --- | --- | --- | --- | --- |
| Development | 2023-24 | 21,032 | 63,096 | 5,361 | 8 |
| Validation (model selection, calibration fit) | 2024-25 | 25,158 | 75,474 | 1,144 | 4 |
| **Held-out test** (reported once, no tuning) | 2025-26 | 25,619 | 76,857 | 1,029 | 3 |

Every model is scored on the identical observations. The empirical-history reference
needs 15 prior games, so it is scored on its own subset (counts shown). Each observation
contributes 5 line-observations to the probabilistic metrics. Prior seasons count toward
history, so 2023-24 (no earlier data loaded) excludes more early-season games.

Runtime: about 2.5 minutes per season on one CPU; 840k predictions per season.

## Held-out results (2025-26), `auto` distribution family

`auto` = Negative Binomial when the 20-game sample variance exceeds 1.05 × mean, else Poisson.

### Points (n = 25,619 player-games; 128,095 line-observations)

| model | MAE | RMSE | bias | NLL | 80% cov | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **ewma** | **4.643** | **6.071** | −0.03 | **3.094** | 0.799 | **0.2336** | **0.660** | **0.032** |
| minutes_adjusted | 4.658 | 6.119 | −0.12 | 3.112 | 0.796 | 0.2390 | 0.674 | 0.047 |
| rolling_10 | 4.714 | 6.175 | −0.02 | 3.126 | 0.790 | 0.2412 | 0.679 | 0.040 |
| season_average | 4.715 | 6.212 | −0.32 | 3.218 | 0.785 | 0.2442 | 0.708 | 0.071 |
| rolling_5 | 4.845 | 6.353 | −0.01 | 3.213 | 0.769 | 0.2541 | 0.726 | 0.094 |
| empirical_history (n=25,182) | 4.728 | 6.166 | −0.03 | 12.18* | 0.810 | 0.2399 | 0.675 | 0.044 |

### Rebounds (n = 25,619)

| model | MAE | RMSE | bias | NLL | 80% cov | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **ewma** | **1.917** | **2.521** | +0.01 | **2.202** | 0.870 | **0.1985** | **0.582** | **0.013** |
| minutes_adjusted | 1.926 | 2.544 | −0.03 | 2.224 | 0.861 | 0.2025 | 0.594 | 0.025 |
| season_average | 1.930 | 2.560 | −0.12 | 2.300 | 0.858 | 0.2046 | 0.618 | 0.039 |
| rolling_10 | 1.949 | 2.567 | +0.01 | 2.231 | 0.856 | 0.2045 | 0.599 | 0.020 |
| rolling_5 | 2.003 | 2.648 | +0.01 | 2.303 | 0.838 | 0.2150 | 0.636 | 0.054 |

### Assists (n = 25,619)

| model | MAE | RMSE | bias | NLL | 80% cov | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **ewma** | **1.361** | **1.845** | −0.01 | **1.802** | 0.899 | **0.1834** | **0.547** | **0.018** |
| minutes_adjusted | 1.369 | 1.866 | −0.03 | 1.840 | 0.891 | 0.1879 | 0.567 | 0.028 |
| season_average | 1.372 | 1.880 | −0.09 | 1.927 | 0.884 | 0.1896 | 0.599 | 0.039 |
| rolling_10 | 1.382 | 1.880 | −0.01 | 1.845 | 0.889 | 0.1895 | 0.570 | 0.027 |
| rolling_5 | 1.412 | 1.927 | 0.00 | 1.984 | 0.868 | 0.1991 | 0.640 | 0.055 |

\* Empirical NLL is inflated by outcomes never seen in the player's last 20 games (pmf = 0,
capped at 30 nats). It is not comparable to the parametric NLLs.

## Is the ranking real? Paired bootstrap (95% CI, 1,000 resamples), held-out season

MAE difference versus `season_average` (negative = better):

| model | points | rebounds | assists |
| --- | --- | --- | --- |
| ewma | −0.072 [−0.092, −0.051] | −0.013 [−0.022, −0.004] | −0.011 [−0.017, −0.005] |
| minutes_adjusted | −0.057 [−0.079, −0.035] | −0.004 [−0.012, +0.006] | −0.003 [−0.009, +0.004] |
| rolling_10 | −0.001 [−0.025, +0.024] | +0.020 [+0.009, +0.030] | +0.010 [+0.003, +0.018] |
| rolling_5 | +0.130 [+0.100, +0.162] | +0.074 [+0.061, +0.086] | +0.041 [+0.031, +0.050] |

Brier difference versus `season_average/auto`:

| configuration | points | rebounds | assists |
| --- | --- | --- | --- |
| ewma/auto | −0.0106 [−0.0120, −0.0092] | −0.0061 [−0.0071, −0.0051] | −0.0062 [−0.0071, −0.0052] |
| minutes_adjusted/auto | −0.0052 [−0.0067, −0.0037] | −0.0021 [−0.0031, −0.0010] | −0.0017 [−0.0027, −0.0007] |
| rolling_10/auto | −0.0030 [−0.0046, −0.0013] | −0.0002 [−0.0014, +0.0010] | −0.0001 [−0.0012, +0.0011] |
| rolling_5/auto | +0.0099 [+0.0078, +0.0123] | +0.0104 [+0.0089, +0.0120] | +0.0095 [+0.0080, +0.0110] |

Reading: EWMA's advantage over the season average is outside sampling noise on every stat
for both point and probabilistic scoring. Its MAE edge is *small in absolute terms* (0.07
points, 0.01 rebounds and assists). `rolling_5` is reliably worst. `minutes_adjusted` is a
clear second on points and indistinguishable from the season average on rebounds and
assists. The same ordering held on the development and validation seasons, so this is not a
one-season artefact; the held-out numbers were looked at once, after selection.

The season average under-forecasts points by 0.3 to 0.5 per game in every season.
Scoring drifts upward within a season and the full-season mean lags it; EWMA does not.

## Distribution family

| | points, ewma | rebounds, ewma | assists, ewma |
| --- | --- | --- | --- |
| auto (NB/Poisson): Brier / ECE / 80% coverage | 0.2336 / 0.032 / 0.80 | 0.1985 / 0.013 / 0.87 | 0.1834 / 0.018 / 0.90 |
| normal (continuity-corrected): Brier / ECE / 80% coverage | 0.2402 / 0.074 / 0.76 | 0.2017 / 0.060 / 0.85 | 0.1886 / 0.069 / 0.88 |

The Normal is markedly worse on reliability for every stat. Its reliability table for points
shows the mechanism: predicted P(more) exceeds the observed frequency in every bin above 0.3
(0.55 predicted → 0.48 observed; 0.85 → 0.68). Count distributions are right-skewed, so the
median sits below the mean; a symmetric Normal centred on the mean overstates the chance of
clearing a line near the median. Negative Binomial captures that skew. The `auto` rule is
therefore retained; "statistically appropriate" here means NB for points and for most
rebound/assist profiles, Poisson only when the 20-game variance is close to the mean.

Interval coverage: the 80% interval covers 80% of points outcomes, but 87% of rebounds and
90% of assists. For low counts the discrete interval cannot land exactly on 80%, and the
(1 + 1/n) variance inflation plus the 0.75 SD floor add width. Intervals for rebounds and
assists are conservative; this is a known, documented over-coverage, not an error.

## Calibration (fitted on 2024-25 only, evaluated on 2025-26)

| stat / configuration | method | Brier | log loss | ECE |
| --- | --- | --- | --- | --- |
| points ewma/auto | uncalibrated | 0.2336 | 0.6600 | 0.0316 |
| | Platt (a = 0.845) | 0.2325 | 0.6574 | 0.0084 |
| | isotonic (87 blocks) | 0.2325 | 0.6576 | **0.0018** |
| rebounds ewma/auto | uncalibrated | 0.1985 | 0.5824 | 0.0130 |
| | Platt (a = 0.902) | 0.1984 | 0.5819 | 0.0101 |
| | isotonic | 0.1984 | 0.5818 | **0.0060** |
| assists ewma/auto | uncalibrated | 0.1834 | 0.5471 | 0.0180 |
| | Platt (a = 0.887) | 0.1832 | 0.5455 | 0.0084 |
| | isotonic | 0.1832 | 0.5446 | **0.0036** |
| points ewma/normal | uncalibrated | 0.2402 | 0.6778 | 0.0738 |
| | isotonic | 0.2332 | 0.6585 | 0.0032 |
| points season_average/auto | uncalibrated | 0.2442 | 0.7083 | 0.0709 |
| | isotonic | 0.2365 | 0.6723 | 0.0092 |

Findings:

* The uncalibrated `ewma/auto` probabilities are **mildly overconfident** (Platt slope
  0.85–0.90 < 1; low bins observe more "more" than predicted, high bins fewer). Points is
  the least reliable: in the 0.8–0.9 bin, 76% of outcomes cleared the line against 84%
  predicted (n = 1,452), and the sparse tails (n < 250) are worse still.
* Calibration fixes **reliability** far more than it improves **scores**: ECE drops by
  5–17×, while Brier improves by only 0.0001–0.0011. The probabilities were already close
  to right on average; they were slightly too spread out.
* Isotonic beats Platt on reliability for every stat and generalised cleanly to the
  held-out season with 126k fitting pairs. Below `MIN_FIT_SIZE` = 2,000 pairs the CLI
  reports calibration as unsupported.
* Calibration can rescue a poor family (Normal + isotonic ≈ auto on points) but never
  beats starting from the better one.

Do not describe anything here as "well calibrated" from the Brier score alone. The claim
supported by the reliability tables is: after isotonic calibration on the validation
season, `ewma/auto` probabilities on the held-out season track observed frequencies within
about 0.01 in every bin with n ≥ 1,000, at synthetic lines, for players with ≥ 10 prior games.

## Where the model is weaker (held-out, `ewma/auto`)

| stratum | points MAE | points ECE | rebounds ECE | assists ECE |
| --- | --- | --- | --- | --- |
| 10–29 prior games (n = 1,710) | 3.88 | 0.066 | 0.048 | 0.048 |
| 30–79 prior games (n = 4,250) | 4.26 | 0.042 | 0.027 | 0.034 |
| 80+ prior games (n = 19,659) | 4.79 | 0.026 | 0.007 | 0.012 |
| recent minutes < 20 (n = 9,358) | 3.63 | 0.043 | 0.029 | 0.035 |
| recent minutes 30+ (n = 6,080) | 5.83 | 0.009 | 0.010 | 0.007 |
| bench by prior starts (n = 13,747) | 4.05 | 0.046 | 0.024 | 0.029 |
| starters (n = 11,872) | 5.34 | 0.016 | 0.006 | 0.006 |

Absolute error grows with minutes and role (more volume, more variance), but reliability
is *best* for starters and heavy-minute players and *worst* for thin histories and bench
players, where minutes are volatile and the model has no information about role changes.
Players with 10–29 prior games are also systematically under-forecast on points (bias
−0.54), consistent with young players improving faster than their average.

## How much skill is this?

Brier of 0.2336 for points against synthetic lines centred on each player's median. The
base rate of "more" at those lines is about 0.45, so a constant forecast would score
roughly 0.247. The baselines therefore extract modest but real information beyond "this
player's recent median", and nothing more. That is the expected ceiling for models that
know nothing about opponent, pace, rest, injuries or lineups. These numbers are the bar
that Phase 3.5 context features and any future ML model must clear on this exact harness.

## Selected configuration

`ewma` projection, `auto` family, isotonic calibration fitted per stat on the most recent
completed season. Chosen on 2024-25; confirmed, not re-tuned, on 2025-26. Default model
in the CLI is already `ewma`; the calibrator is not yet wired into `predict()` (next step).

## Limitations

1. Synthetic lines. Real PrizePicks lines embed market information and would be harder to
   beat; nothing here speaks to that.
2. Conditional on playing. Targets are games the player played; DNP risk is outside the model.
3. Mild overconfidence remains for points without calibration; tails (P < 0.1 or > 0.9)
   are sparse and poorly estimated.
4. Over-wide intervals for rebounds and assists.
5. No context features, so bench/volatile-minutes players are the weak spot.
6. Three seasons from one provider; licensing of the underlying data is unresolved
   (`docs/LICENSING_RISK.md`).
7. Replayed history is not prospective validation. 2026-27 predictions should be logged
   with timestamps before tip-off to test the system for real.

## Reproduce

```bash
uv run ppp ingest-sdv --season 2024 --season 2025 --season 2026
uv run ppp backtest --season 2023-24 --run-name dev_2023-24
uv run ppp backtest --season 2024-25 --run-name val_2024-25
uv run ppp backtest --season 2025-26 --run-name test_2025-26
for s in points rebounds assists; do
  uv run ppp calibrate --fit-run data/backtests/val_2024-25 --eval-run data/backtests/test_2025-26 --stat $s
done
```
