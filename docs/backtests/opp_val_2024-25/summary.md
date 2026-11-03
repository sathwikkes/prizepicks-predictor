# Backtest summary: 2024-25

Run started 2026-11-03T19:18:08.294131+00:00 · 56.1s · package 0.1.0

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
| ewma_opponent | auto | 1.361 | 1.846 | -0.011 | 1.806 | 0.895 | 4.1 | 0.1844 | 0.5504 | 0.0207 |

- best MAE: **ewma_opponent** (1.361); best RMSE: **ewma_opponent** (1.846)
- best NLL: **ewma_opponent/auto** (1.806); best Brier: **ewma_opponent/auto** (0.1844)
- lowest ECE (reliability): **ewma_opponent/auto** (0.0207); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):


Brier difference vs season_average/auto (paired bootstrap 95% CI):


## points

n = 25158 player-games, 125790 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ewma | auto | 4.683 | 6.127 | -0.100 | 3.089 | 0.798 | 13.8 | 0.2337 | 0.6604 | 0.0306 |
| ewma_opponent | auto | 4.677 | 6.119 | -0.101 | 3.087 | 0.799 | 13.8 | 0.2333 | 0.6597 | 0.0309 |

- best MAE: **ewma_opponent** (4.677); best RMSE: **ewma_opponent** (6.119)
- best NLL: **ewma_opponent/auto** (3.087); best Brier: **ewma_opponent/auto** (0.2333)
- lowest ECE (reliability): **ewma/auto** (0.0306); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):


Brier difference vs season_average/auto (paired bootstrap 95% CI):


## rebounds

n = 25158 player-games, 125790 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ewma | auto | 1.947 | 2.566 | -0.028 | 2.216 | 0.860 | 5.9 | 0.2011 | 0.5891 | 0.0161 |
| ewma_opponent | auto | 1.946 | 2.565 | -0.030 | 2.216 | 0.860 | 5.9 | 0.2010 | 0.5889 | 0.0161 |

- best MAE: **ewma_opponent** (1.946); best RMSE: **ewma_opponent** (2.565)
- best NLL: **ewma_opponent/auto** (2.216); best Brier: **ewma_opponent/auto** (0.2010)
- lowest ECE (reliability): **ewma/auto** (0.0161); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):


Brier difference vs season_average/auto (paired bootstrap 95% CI):

