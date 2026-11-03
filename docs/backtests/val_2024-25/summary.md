# Backtest summary: 2024-25

Run started 2026-11-02T18:52:03.455349+00:00 · 147.16s · package 0.1.0

Lines are **synthetic evaluation thresholds** (trailing-10-game median ± offsets, pregame-only, identical for every model). They are not PrizePicks lines and say nothing about wagering.

## Cohort

- candidate player-games: 25158; observations evaluated: 75474
- exclusions: {'insufficient_prior_games': 1144, 'target_zero_minutes': 4}
- missing predictions (configuration → count): {'empirical_history/empirical': 1635}
- common cohort per stat (observations every configuration predicted): {'assists': 25158, 'points': 25158, 'rebounds': 25158}

## assists

n = 25158 player-games, 123065 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 1.386 | 1.874 | -0.010 | 4.170 | 0.874 | 4.0 | 0.1884 | 0.6014 | 0.0305 |
| ewma | auto | 1.364 | 1.851 | -0.011 | 1.807 | 0.895 | 4.1 | 0.1848 | 0.5513 | 0.0208 |
| ewma | normal | 1.364 | 1.851 | -0.011 | 1.882 | 0.883 | 4.0 | 0.1891 | 0.5627 | 0.0685 |
| minutes_adjusted | auto | 1.375 | 1.872 | -0.038 | 1.844 | 0.885 | 4.1 | 0.1897 | 0.5716 | 0.0330 |
| minutes_adjusted | normal | 1.375 | 1.872 | -0.038 | 1.888 | 0.878 | 4.0 | 0.1900 | 0.5654 | 0.0666 |
| rolling_10 | auto | 1.388 | 1.885 | -0.010 | 1.850 | 0.885 | 4.1 | 0.1912 | 0.5752 | 0.0302 |
| rolling_10 | normal | 1.388 | 1.885 | -0.010 | 1.897 | 0.876 | 4.0 | 0.1916 | 0.5696 | 0.0699 |
| rolling_5 | auto | 1.421 | 1.942 | -0.008 | 1.995 | 0.864 | 4.0 | 0.2015 | 0.6486 | 0.0576 |
| rolling_5 | normal | 1.421 | 1.942 | -0.008 | 1.916 | 0.862 | 3.9 | 0.1953 | 0.5812 | 0.0758 |
| season_average | auto | 1.366 | 1.876 | -0.132 | 1.909 | 0.886 | 4.0 | 0.1898 | 0.5953 | 0.0436 |
| season_average | normal | 1.366 | 1.876 | -0.132 | 1.884 | 0.881 | 3.9 | 0.1887 | 0.5639 | 0.0606 |

- best MAE: **ewma** (1.364); best RMSE: **ewma** (1.851)
- best NLL: **ewma/auto** (1.807); best Brier: **ewma/auto** (0.1848)
- lowest ECE (reliability): **ewma/auto** (0.0208); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.003 [-0.009, +0.003] (within sampling noise)
- minutes_adjusted: +0.008 [+0.002, +0.015] (meaningful)
- rolling_10: +0.022 [+0.014, +0.028] (meaningful)
- rolling_5: +0.054 [+0.045, +0.064] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0050 [-0.0059, -0.0041] (meaningful)
- ewma/normal: -0.0007 [-0.0022, +0.0007] (within sampling noise)
- minutes_adjusted/auto: -0.0001 [-0.0011, +0.0009] (within sampling noise)
- minutes_adjusted/normal: +0.0002 [-0.0013, +0.0015] (within sampling noise)
- rolling_10/auto: +0.0014 [+0.0002, +0.0024] (meaningful)
- rolling_10/normal: +0.0018 [+0.0004, +0.0032] (meaningful)
- rolling_5/auto: +0.0118 [+0.0103, +0.0132] (meaningful)
- rolling_5/normal: +0.0055 [+0.0039, +0.0070] (meaningful)
- season_average/normal: -0.0011 [-0.0023, +0.0000] (within sampling noise)

## points

n = 25158 player-games, 123065 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 4.774 | 6.226 | -0.130 | 12.262 | 0.805 | 13.8 | 0.2403 | 0.6765 | 0.0431 |
| ewma | auto | 4.683 | 6.127 | -0.100 | 3.089 | 0.798 | 13.8 | 0.2337 | 0.6604 | 0.0306 |
| ewma | normal | 4.683 | 6.127 | -0.100 | 3.171 | 0.762 | 13.6 | 0.2412 | 0.6806 | 0.0758 |
| minutes_adjusted | auto | 4.718 | 6.189 | -0.197 | 3.113 | 0.789 | 13.6 | 0.2396 | 0.6760 | 0.0469 |
| minutes_adjusted | normal | 4.718 | 6.189 | -0.197 | 3.173 | 0.761 | 13.5 | 0.2422 | 0.6824 | 0.0723 |
| rolling_10 | auto | 4.768 | 6.233 | -0.080 | 3.122 | 0.784 | 13.7 | 0.2417 | 0.6800 | 0.0399 |
| rolling_10 | normal | 4.768 | 6.233 | -0.080 | 3.185 | 0.756 | 13.5 | 0.2448 | 0.6878 | 0.0788 |
| rolling_5 | auto | 4.898 | 6.433 | -0.047 | 3.240 | 0.763 | 13.4 | 0.2548 | 0.7401 | 0.0952 |
| rolling_5 | normal | 4.898 | 6.433 | -0.047 | 3.207 | 0.749 | 13.5 | 0.2507 | 0.7032 | 0.0937 |
| season_average | auto | 4.743 | 6.236 | -0.513 | 3.204 | 0.787 | 13.5 | 0.2439 | 0.7123 | 0.0739 |
| season_average | normal | 4.743 | 6.236 | -0.513 | 3.181 | 0.758 | 13.4 | 0.2432 | 0.6886 | 0.0729 |

- best MAE: **ewma** (4.683); best RMSE: **ewma** (6.127)
- best NLL: **ewma/auto** (3.089); best Brier: **ewma/auto** (0.2337)
- lowest ECE (reliability): **ewma/auto** (0.0306); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.060 [-0.081, -0.038] (meaningful)
- minutes_adjusted: -0.025 [-0.049, -0.002] (meaningful)
- rolling_10: +0.025 [-0.000, +0.050] (within sampling noise)
- rolling_5: +0.155 [+0.122, +0.187] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0102 [-0.0116, -0.0087] (meaningful)
- ewma/normal: -0.0027 [-0.0046, -0.0006] (meaningful)
- minutes_adjusted/auto: -0.0043 [-0.0059, -0.0027] (meaningful)
- minutes_adjusted/normal: -0.0017 [-0.0037, +0.0004] (within sampling noise)
- rolling_10/auto: -0.0022 [-0.0039, -0.0004] (meaningful)
- rolling_10/normal: +0.0009 [-0.0012, +0.0032] (within sampling noise)
- rolling_5/auto: +0.0109 [+0.0085, +0.0133] (meaningful)
- rolling_5/normal: +0.0068 [+0.0043, +0.0094] (meaningful)
- season_average/normal: -0.0007 [-0.0022, +0.0008] (within sampling noise)

## rebounds

n = 25158 player-games, 123065 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 1.980 | 2.606 | -0.030 | 5.899 | 0.843 | 5.8 | 0.2065 | 0.6260 | 0.0340 |
| ewma | auto | 1.947 | 2.566 | -0.028 | 2.216 | 0.860 | 5.9 | 0.2011 | 0.5891 | 0.0161 |
| ewma | normal | 1.947 | 2.566 | -0.028 | 2.288 | 0.841 | 5.7 | 0.2044 | 0.5988 | 0.0590 |
| minutes_adjusted | auto | 1.956 | 2.584 | -0.068 | 2.239 | 0.854 | 5.9 | 0.2054 | 0.6017 | 0.0294 |
| minutes_adjusted | normal | 1.956 | 2.584 | -0.068 | 2.291 | 0.838 | 5.7 | 0.2050 | 0.6006 | 0.0566 |
| rolling_10 | auto | 1.979 | 2.611 | -0.024 | 2.246 | 0.850 | 5.9 | 0.2073 | 0.6062 | 0.0256 |
| rolling_10 | normal | 1.979 | 2.611 | -0.024 | 2.304 | 0.834 | 5.7 | 0.2073 | 0.6059 | 0.0605 |
| rolling_5 | auto | 2.032 | 2.684 | -0.017 | 2.326 | 0.832 | 5.8 | 0.2174 | 0.6483 | 0.0571 |
| rolling_5 | normal | 2.032 | 2.684 | -0.017 | 2.325 | 0.823 | 5.7 | 0.2122 | 0.6203 | 0.0701 |
| season_average | auto | 1.962 | 2.609 | -0.134 | 2.303 | 0.850 | 5.8 | 0.2067 | 0.6247 | 0.0395 |
| season_average | normal | 1.962 | 2.609 | -0.134 | 2.295 | 0.837 | 5.7 | 0.2053 | 0.6031 | 0.0564 |

- best MAE: **ewma** (1.947); best RMSE: **ewma** (2.566)
- best NLL: **ewma/auto** (2.216); best Brier: **ewma/auto** (0.2011)
- lowest ECE (reliability): **ewma/auto** (0.0161); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.015 [-0.024, -0.007] (meaningful)
- minutes_adjusted: -0.006 [-0.016, +0.002] (within sampling noise)
- rolling_10: +0.016 [+0.005, +0.026] (meaningful)
- rolling_5: +0.070 [+0.056, +0.083] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0056 [-0.0066, -0.0045] (meaningful)
- ewma/normal: -0.0023 [-0.0037, -0.0010] (meaningful)
- minutes_adjusted/auto: -0.0013 [-0.0024, -0.0002] (meaningful)
- minutes_adjusted/normal: -0.0017 [-0.0030, -0.0003] (meaningful)
- rolling_10/auto: +0.0006 [-0.0006, +0.0019] (within sampling noise)
- rolling_10/normal: +0.0006 [-0.0009, +0.0020] (within sampling noise)
- rolling_5/auto: +0.0107 [+0.0089, +0.0124] (meaningful)
- rolling_5/normal: +0.0055 [+0.0038, +0.0071] (meaningful)
- season_average/normal: -0.0015 [-0.0024, -0.0005] (meaningful)
