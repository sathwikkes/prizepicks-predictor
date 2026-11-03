# Backtest summary: 2024-25

Run started 2026-11-03T06:47:26.243639+00:00 · 71.64s · package 0.1.0

Lines are **synthetic evaluation thresholds** (trailing-10-game median ± offsets, pregame-only, identical for every model). They are not PrizePicks lines and say nothing about wagering.

## Cohort

- candidate player-games: 25158; observations evaluated: 75474
- exclusions: {'insufficient_prior_games': 1144, 'target_zero_minutes': 4}
- missing predictions (configuration → count): none
- common cohort per stat (observations every configuration predicted): {'assists': 25158, 'points': 25158, 'rebounds': 25158}

## assists

n = 25158 player-games, 125790 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ewma | auto | 1.364 | 1.851 | -0.011 | 1.807 | 0.895 | 4.1 | 0.1848 | 0.5513 | 0.0208 |
| ewma_schedule | auto | 1.363 | 1.850 | -0.018 | 1.807 | 0.894 | 4.1 | 0.1848 | 0.5514 | 0.0221 |

- best MAE: **ewma_schedule** (1.363); best RMSE: **ewma_schedule** (1.850)
- best NLL: **ewma/auto** (1.807); best Brier: **ewma/auto** (0.1848)
- lowest ECE (reliability): **ewma/auto** (0.0208); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):


Brier difference vs season_average/auto (paired bootstrap 95% CI):


## points

n = 25158 player-games, 125790 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ewma | auto | 4.683 | 6.127 | -0.100 | 3.089 | 0.798 | 13.8 | 0.2337 | 0.6604 | 0.0306 |
| ewma_schedule | auto | 4.685 | 6.129 | -0.081 | 3.089 | 0.797 | 13.8 | 0.2336 | 0.6603 | 0.0293 |

- best MAE: **ewma** (4.683); best RMSE: **ewma** (6.127)
- best NLL: **ewma/auto** (3.089); best Brier: **ewma_schedule/auto** (0.2336)
- lowest ECE (reliability): **ewma_schedule/auto** (0.0293); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):


Brier difference vs season_average/auto (paired bootstrap 95% CI):


## rebounds

n = 25158 player-games, 125790 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ewma | auto | 1.947 | 2.566 | -0.028 | 2.216 | 0.860 | 5.9 | 0.2011 | 0.5891 | 0.0161 |
| ewma_schedule | auto | 1.951 | 2.567 | 0.003 | 2.216 | 0.860 | 5.9 | 0.2010 | 0.5890 | 0.0139 |

- best MAE: **ewma** (1.947); best RMSE: **ewma** (2.566)
- best NLL: **ewma/auto** (2.216); best Brier: **ewma_schedule/auto** (0.2010)
- lowest ECE (reliability): **ewma_schedule/auto** (0.0139); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):


Brier difference vs season_average/auto (paired bootstrap 95% CI):

