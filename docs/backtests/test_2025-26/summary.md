# Backtest summary: 2025-26

Run started 2026-11-02T18:55:40.063071+00:00 · 152.67s · package 0.1.0

Lines are **synthetic evaluation thresholds** (trailing-10-game median ± offsets, pregame-only, identical for every model). They are not PrizePicks lines and say nothing about wagering.

## Cohort

- candidate player-games: 25619; observations evaluated: 76857
- exclusions: {'target_zero_minutes': 3, 'insufficient_prior_games': 1029}
- missing predictions (configuration → count): {'empirical_history/empirical': 1311}
- common cohort per stat (observations every configuration predicted): {'assists': 25619, 'points': 25619, 'rebounds': 25619}

## assists

n = 25619 player-games, 125910 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 1.380 | 1.869 | -0.011 | 4.130 | 0.876 | 3.9 | 0.1880 | 0.5961 | 0.0313 |
| ewma | auto | 1.361 | 1.845 | -0.010 | 1.802 | 0.899 | 4.1 | 0.1834 | 0.5471 | 0.0180 |
| ewma | normal | 1.361 | 1.845 | -0.010 | 1.882 | 0.884 | 4.0 | 0.1886 | 0.5604 | 0.0694 |
| minutes_adjusted | auto | 1.369 | 1.866 | -0.027 | 1.840 | 0.891 | 4.1 | 0.1879 | 0.5667 | 0.0279 |
| minutes_adjusted | normal | 1.369 | 1.866 | -0.027 | 1.887 | 0.881 | 3.9 | 0.1892 | 0.5624 | 0.0678 |
| rolling_10 | auto | 1.382 | 1.880 | -0.006 | 1.845 | 0.889 | 4.1 | 0.1895 | 0.5704 | 0.0269 |
| rolling_10 | normal | 1.382 | 1.880 | -0.006 | 1.895 | 0.878 | 4.0 | 0.1909 | 0.5666 | 0.0703 |
| rolling_5 | auto | 1.412 | 1.927 | -0.002 | 1.984 | 0.868 | 4.0 | 0.1991 | 0.6400 | 0.0550 |
| rolling_5 | normal | 1.412 | 1.927 | -0.002 | 1.913 | 0.865 | 3.9 | 0.1941 | 0.5773 | 0.0762 |
| season_average | auto | 1.372 | 1.880 | -0.093 | 1.927 | 0.884 | 4.0 | 0.1896 | 0.5993 | 0.0393 |
| season_average | normal | 1.372 | 1.880 | -0.093 | 1.889 | 0.879 | 3.9 | 0.1894 | 0.5646 | 0.0665 |

- best MAE: **ewma** (1.361); best RMSE: **ewma** (1.845)
- best NLL: **ewma/auto** (1.802); best Brier: **ewma/auto** (0.1834)
- lowest ECE (reliability): **ewma/auto** (0.0180); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.011 [-0.017, -0.005] (meaningful)
- minutes_adjusted: -0.003 [-0.009, +0.004] (within sampling noise)
- rolling_10: +0.010 [+0.003, +0.018] (meaningful)
- rolling_5: +0.041 [+0.031, +0.050] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0062 [-0.0071, -0.0052] (meaningful)
- ewma/normal: -0.0010 [-0.0025, +0.0005] (within sampling noise)
- minutes_adjusted/auto: -0.0017 [-0.0027, -0.0007] (meaningful)
- minutes_adjusted/normal: -0.0004 [-0.0019, +0.0010] (within sampling noise)
- rolling_10/auto: -0.0001 [-0.0012, +0.0011] (within sampling noise)
- rolling_10/normal: +0.0013 [-0.0002, +0.0028] (within sampling noise)
- rolling_5/auto: +0.0095 [+0.0080, +0.0110] (meaningful)
- rolling_5/normal: +0.0045 [+0.0029, +0.0062] (meaningful)
- season_average/normal: -0.0002 [-0.0014, +0.0009] (within sampling noise)

## points

n = 25619 player-games, 125910 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 4.728 | 6.166 | -0.034 | 12.184 | 0.810 | 13.8 | 0.2399 | 0.6753 | 0.0437 |
| ewma | auto | 4.643 | 6.071 | -0.032 | 3.094 | 0.799 | 13.8 | 0.2336 | 0.6600 | 0.0316 |
| ewma | normal | 4.643 | 6.071 | -0.032 | 3.155 | 0.762 | 13.6 | 0.2402 | 0.6778 | 0.0738 |
| minutes_adjusted | auto | 4.658 | 6.119 | -0.115 | 3.112 | 0.796 | 13.7 | 0.2390 | 0.6742 | 0.0465 |
| minutes_adjusted | normal | 4.658 | 6.119 | -0.115 | 3.157 | 0.764 | 13.6 | 0.2406 | 0.6785 | 0.0705 |
| rolling_10 | auto | 4.714 | 6.175 | -0.019 | 3.126 | 0.790 | 13.7 | 0.2412 | 0.6785 | 0.0397 |
| rolling_10 | normal | 4.714 | 6.175 | -0.019 | 3.169 | 0.758 | 13.6 | 0.2433 | 0.6838 | 0.0758 |
| rolling_5 | auto | 4.845 | 6.353 | -0.010 | 3.213 | 0.769 | 13.5 | 0.2541 | 0.7263 | 0.0944 |
| rolling_5 | normal | 4.845 | 6.353 | -0.010 | 3.187 | 0.751 | 13.5 | 0.2490 | 0.6987 | 0.0907 |
| season_average | auto | 4.715 | 6.212 | -0.317 | 3.218 | 0.785 | 13.5 | 0.2442 | 0.7083 | 0.0709 |
| season_average | normal | 4.715 | 6.212 | -0.317 | 3.169 | 0.759 | 13.5 | 0.2431 | 0.6879 | 0.0782 |

- best MAE: **ewma** (4.643); best RMSE: **ewma** (6.071)
- best NLL: **ewma/auto** (3.094); best Brier: **ewma/auto** (0.2336)
- lowest ECE (reliability): **ewma/auto** (0.0316); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.072 [-0.092, -0.051] (meaningful)
- minutes_adjusted: -0.057 [-0.079, -0.035] (meaningful)
- rolling_10: -0.001 [-0.025, +0.024] (within sampling noise)
- rolling_5: +0.130 [+0.100, +0.162] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0106 [-0.0120, -0.0092] (meaningful)
- ewma/normal: -0.0040 [-0.0060, -0.0020] (meaningful)
- minutes_adjusted/auto: -0.0052 [-0.0067, -0.0037] (meaningful)
- minutes_adjusted/normal: -0.0036 [-0.0055, -0.0015] (meaningful)
- rolling_10/auto: -0.0030 [-0.0046, -0.0013] (meaningful)
- rolling_10/normal: -0.0009 [-0.0030, +0.0012] (within sampling noise)
- rolling_5/auto: +0.0099 [+0.0078, +0.0123] (meaningful)
- rolling_5/normal: +0.0048 [+0.0025, +0.0072] (meaningful)
- season_average/normal: -0.0011 [-0.0026, +0.0003] (within sampling noise)

## rebounds

n = 25619 player-games, 125910 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 1.940 | 2.549 | 0.014 | 5.860 | 0.851 | 5.8 | 0.2035 | 0.6176 | 0.0293 |
| ewma | auto | 1.917 | 2.521 | 0.009 | 2.202 | 0.870 | 5.9 | 0.1985 | 0.5824 | 0.0130 |
| ewma | normal | 1.917 | 2.521 | 0.009 | 2.265 | 0.850 | 5.7 | 0.2017 | 0.5914 | 0.0600 |
| minutes_adjusted | auto | 1.926 | 2.544 | -0.031 | 2.224 | 0.861 | 5.9 | 0.2025 | 0.5938 | 0.0246 |
| minutes_adjusted | normal | 1.926 | 2.544 | -0.031 | 2.268 | 0.846 | 5.7 | 0.2022 | 0.5930 | 0.0578 |
| rolling_10 | auto | 1.949 | 2.567 | 0.010 | 2.231 | 0.856 | 5.9 | 0.2045 | 0.5986 | 0.0204 |
| rolling_10 | normal | 1.949 | 2.567 | 0.010 | 2.279 | 0.841 | 5.7 | 0.2045 | 0.5982 | 0.0616 |
| rolling_5 | auto | 2.003 | 2.648 | 0.007 | 2.303 | 0.838 | 5.8 | 0.2150 | 0.6359 | 0.0541 |
| rolling_5 | normal | 2.003 | 2.648 | 0.007 | 2.302 | 0.830 | 5.7 | 0.2094 | 0.6123 | 0.0681 |
| season_average | auto | 1.930 | 2.560 | -0.119 | 2.300 | 0.858 | 5.8 | 0.2046 | 0.6181 | 0.0394 |
| season_average | normal | 1.930 | 2.560 | -0.119 | 2.270 | 0.845 | 5.7 | 0.2023 | 0.5951 | 0.0538 |

- best MAE: **ewma** (1.917); best RMSE: **ewma** (2.521)
- best NLL: **ewma/auto** (2.202); best Brier: **ewma/auto** (0.1985)
- lowest ECE (reliability): **ewma/auto** (0.0130); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.013 [-0.022, -0.004] (meaningful)
- minutes_adjusted: -0.004 [-0.012, +0.006] (within sampling noise)
- rolling_10: +0.020 [+0.009, +0.030] (meaningful)
- rolling_5: +0.074 [+0.061, +0.086] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0061 [-0.0071, -0.0051] (meaningful)
- ewma/normal: -0.0030 [-0.0044, -0.0017] (meaningful)
- minutes_adjusted/auto: -0.0021 [-0.0031, -0.0010] (meaningful)
- minutes_adjusted/normal: -0.0024 [-0.0038, -0.0010] (meaningful)
- rolling_10/auto: -0.0002 [-0.0014, +0.0010] (within sampling noise)
- rolling_10/normal: -0.0001 [-0.0015, +0.0013] (within sampling noise)
- rolling_5/auto: +0.0104 [+0.0089, +0.0120] (meaningful)
- rolling_5/normal: +0.0047 [+0.0032, +0.0063] (meaningful)
- season_average/normal: -0.0023 [-0.0033, -0.0014] (meaningful)
