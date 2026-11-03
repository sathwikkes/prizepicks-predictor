# Backtest summary: 2023-24

Run started 2026-11-02T18:49:07.947857+00:00 · 117.21s · package 0.1.0

Lines are **synthetic evaluation thresholds** (trailing-10-game median ± offsets, pregame-only, identical for every model). They are not PrizePicks lines and say nothing about wagering.

## Cohort

- candidate player-games: 21032; observations evaluated: 63096
- exclusions: {'insufficient_prior_games': 5361, 'target_zero_minutes': 8}
- missing predictions (configuration → count): {'empirical_history/empirical': 7230}
- common cohort per stat (observations every configuration predicted): {'assists': 21032, 'points': 21032, 'rebounds': 21032}

## assists

n = 21032 player-games, 93110 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 1.403 | 1.908 | -0.074 | 4.323 | 0.870 | 4.0 | 0.1895 | 0.6037 | 0.0303 |
| ewma | auto | 1.382 | 1.881 | -0.051 | 1.819 | 0.890 | 4.1 | 0.1869 | 0.5552 | 0.0236 |
| ewma | normal | 1.382 | 1.881 | -0.051 | 1.898 | 0.877 | 4.0 | 0.1904 | 0.5661 | 0.0642 |
| minutes_adjusted | auto | 1.398 | 1.905 | -0.054 | 1.849 | 0.884 | 4.1 | 0.1910 | 0.5712 | 0.0318 |
| minutes_adjusted | normal | 1.398 | 1.905 | -0.054 | 1.908 | 0.872 | 4.0 | 0.1923 | 0.5711 | 0.0657 |
| rolling_10 | auto | 1.402 | 1.912 | -0.040 | 1.852 | 0.883 | 4.1 | 0.1918 | 0.5732 | 0.0313 |
| rolling_10 | normal | 1.402 | 1.912 | -0.040 | 1.911 | 0.872 | 4.0 | 0.1926 | 0.5720 | 0.0666 |
| rolling_5 | auto | 1.446 | 1.970 | -0.020 | 2.004 | 0.862 | 4.0 | 0.2028 | 0.6512 | 0.0583 |
| rolling_5 | normal | 1.446 | 1.970 | -0.020 | 1.935 | 0.858 | 3.9 | 0.1974 | 0.5863 | 0.0752 |
| season_average | auto | 1.397 | 1.915 | -0.135 | 1.831 | 0.885 | 4.1 | 0.1897 | 0.5620 | 0.0368 |
| season_average | normal | 1.397 | 1.915 | -0.135 | 1.910 | 0.872 | 3.9 | 0.1927 | 0.5736 | 0.0625 |

- best MAE: **ewma** (1.382); best RMSE: **ewma** (1.881)
- best NLL: **ewma/auto** (1.819); best Brier: **ewma/auto** (0.1869)
- lowest ECE (reliability): **ewma/auto** (0.0236); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.015 [-0.021, -0.008] (meaningful)
- minutes_adjusted: +0.001 [-0.006, +0.009] (within sampling noise)
- rolling_10: +0.005 [-0.003, +0.014] (within sampling noise)
- rolling_5: +0.049 [+0.038, +0.059] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0028 [-0.0038, -0.0019] (meaningful)
- ewma/normal: +0.0008 [-0.0005, +0.0021] (within sampling noise)
- minutes_adjusted/auto: +0.0014 [+0.0002, +0.0024] (meaningful)
- minutes_adjusted/normal: +0.0026 [+0.0012, +0.0039] (meaningful)
- rolling_10/auto: +0.0021 [+0.0009, +0.0033] (meaningful)
- rolling_10/normal: +0.0030 [+0.0015, +0.0044] (meaningful)
- rolling_5/auto: +0.0131 [+0.0112, +0.0149] (meaningful)
- rolling_5/normal: +0.0077 [+0.0060, +0.0093] (meaningful)
- season_average/normal: +0.0030 [+0.0019, +0.0041] (meaningful)

## points

n = 21032 player-games, 93110 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 4.764 | 6.239 | -0.174 | 12.373 | 0.801 | 13.9 | 0.2383 | 0.6726 | 0.0392 |
| ewma | auto | 4.656 | 6.118 | -0.128 | 3.096 | 0.791 | 13.6 | 0.2342 | 0.6620 | 0.0350 |
| ewma | normal | 4.656 | 6.118 | -0.128 | 3.162 | 0.755 | 13.4 | 0.2393 | 0.6755 | 0.0662 |
| minutes_adjusted | auto | 4.714 | 6.197 | -0.160 | 3.118 | 0.783 | 13.6 | 0.2397 | 0.6749 | 0.0444 |
| minutes_adjusted | normal | 4.714 | 6.197 | -0.160 | 3.173 | 0.752 | 13.4 | 0.2423 | 0.6819 | 0.0665 |
| rolling_10 | auto | 4.738 | 6.218 | -0.103 | 3.126 | 0.781 | 13.6 | 0.2416 | 0.6790 | 0.0415 |
| rolling_10 | normal | 4.738 | 6.218 | -0.103 | 3.177 | 0.752 | 13.4 | 0.2433 | 0.6837 | 0.0693 |
| rolling_5 | auto | 4.855 | 6.402 | -0.056 | 3.208 | 0.762 | 13.4 | 0.2530 | 0.7227 | 0.0910 |
| rolling_5 | normal | 4.855 | 6.402 | -0.056 | 3.199 | 0.746 | 13.4 | 0.2485 | 0.6972 | 0.0886 |
| season_average | auto | 4.731 | 6.210 | -0.326 | 3.125 | 0.785 | 13.6 | 0.2409 | 0.6805 | 0.0621 |
| season_average | normal | 4.731 | 6.210 | -0.326 | 3.179 | 0.751 | 13.4 | 0.2434 | 0.6883 | 0.0715 |

- best MAE: **ewma** (4.656); best RMSE: **ewma** (6.118)
- best NLL: **ewma/auto** (3.096); best Brier: **ewma/auto** (0.2342)
- lowest ECE (reliability): **ewma/auto** (0.0350); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.075 [-0.095, -0.055] (meaningful)
- minutes_adjusted: -0.017 [-0.039, +0.007] (within sampling noise)
- rolling_10: +0.007 [-0.017, +0.031] (within sampling noise)
- rolling_5: +0.124 [+0.089, +0.160] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0066 [-0.0079, -0.0052] (meaningful)
- ewma/normal: -0.0015 [-0.0032, +0.0001] (within sampling noise)
- minutes_adjusted/auto: -0.0012 [-0.0026, +0.0004] (within sampling noise)
- minutes_adjusted/normal: +0.0014 [-0.0003, +0.0032] (within sampling noise)
- rolling_10/auto: +0.0007 [-0.0009, +0.0024] (within sampling noise)
- rolling_10/normal: +0.0025 [+0.0006, +0.0043] (meaningful)
- rolling_5/auto: +0.0122 [+0.0098, +0.0147] (meaningful)
- rolling_5/normal: +0.0076 [+0.0055, +0.0099] (meaningful)
- season_average/normal: +0.0025 [+0.0013, +0.0038] (meaningful)

## rebounds

n = 21032 player-games, 93110 line-observations per configuration

| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | log loss | ECE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| empirical_history | empirical | 1.941 | 2.580 | -0.064 | 5.933 | 0.847 | 5.7 | 0.2031 | 0.6215 | 0.0309 |
| ewma | auto | 1.912 | 2.539 | -0.048 | 2.204 | 0.861 | 5.8 | 0.1988 | 0.5839 | 0.0182 |
| ewma | normal | 1.912 | 2.539 | -0.048 | 2.276 | 0.843 | 5.6 | 0.2010 | 0.5907 | 0.0521 |
| minutes_adjusted | auto | 1.932 | 2.564 | -0.061 | 2.221 | 0.856 | 5.8 | 0.2024 | 0.5935 | 0.0245 |
| minutes_adjusted | normal | 1.932 | 2.564 | -0.061 | 2.284 | 0.839 | 5.6 | 0.2029 | 0.5955 | 0.0527 |
| rolling_10 | auto | 1.941 | 2.575 | -0.040 | 2.229 | 0.853 | 5.8 | 0.2041 | 0.5977 | 0.0241 |
| rolling_10 | normal | 1.941 | 2.575 | -0.040 | 2.289 | 0.838 | 5.6 | 0.2037 | 0.5974 | 0.0546 |
| rolling_5 | auto | 2.001 | 2.656 | -0.021 | 2.297 | 0.834 | 5.7 | 0.2141 | 0.6347 | 0.0530 |
| rolling_5 | normal | 2.001 | 2.656 | -0.021 | 2.314 | 0.825 | 5.5 | 0.2091 | 0.6126 | 0.0653 |
| season_average | auto | 1.937 | 2.577 | -0.122 | 2.220 | 0.859 | 5.8 | 0.2022 | 0.5931 | 0.0317 |
| season_average | normal | 1.937 | 2.577 | -0.122 | 2.289 | 0.838 | 5.6 | 0.2036 | 0.5986 | 0.0528 |

- best MAE: **ewma** (1.912); best RMSE: **ewma** (2.539)
- best NLL: **ewma/auto** (2.204); best Brier: **ewma/auto** (0.1988)
- lowest ECE (reliability): **ewma/auto** (0.0182); inspect the reliability diagram before calling anything calibrated

MAE difference vs season_average (paired bootstrap 95% CI; negative = better):

- ewma: -0.025 [-0.034, -0.017] (meaningful)
- minutes_adjusted: -0.005 [-0.015, +0.005] (within sampling noise)
- rolling_10: +0.004 [-0.007, +0.014] (within sampling noise)
- rolling_5: +0.064 [+0.049, +0.078] (meaningful)

Brier difference vs season_average/auto (paired bootstrap 95% CI):

- ewma/auto: -0.0035 [-0.0045, -0.0025] (meaningful)
- ewma/normal: -0.0012 [-0.0023, -0.0000] (meaningful)
- minutes_adjusted/auto: +0.0002 [-0.0009, +0.0014] (within sampling noise)
- minutes_adjusted/normal: +0.0007 [-0.0006, +0.0019] (within sampling noise)
- rolling_10/auto: +0.0019 [+0.0007, +0.0032] (meaningful)
- rolling_10/normal: +0.0015 [+0.0002, +0.0028] (meaningful)
- rolling_5/auto: +0.0119 [+0.0100, +0.0138] (meaningful)
- rolling_5/normal: +0.0069 [+0.0052, +0.0085] (meaningful)
- season_average/normal: +0.0013 [+0.0005, +0.0021] (meaningful)
