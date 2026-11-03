# Phase 4C: opponent pace and defence context — result

Date: 2026-11-03. Verdict: **`ewma_opponent` is a small, consistent, statistically
detectable improvement over EWMA on the validation season, but the gain (about 0.1% of
MAE) is below the practical-significance bar. EWMA stays the production default; the
opponent model is research-only and is the first contextual feature with a validated
positive effect across all three statistics.**

## Data

Team box-score totals come from the SportsDataverse team game logs already cached for
box-score ingestion (`team_game_logs_{season}.csv`, NBA.com data). `ppp ingest-team-stats`
loads them into `team_game_stats`, keyed to existing games by NBA game id: 2,460 team-games
per completed season (playoff rows are skipped because playoff box scores are not loaded),
184 for 2026-27 so far. No new download, no new provider.

## Features (`prediction/opponent_context.py`, all pregame)

| Quantity | Definition |
| --- | --- |
| possessions | FGA − OREB + TOV + 0.44·FTA |
| pace48 | game possessions × 48 / (team minutes / 5), so overtime does not inflate pace |
| defensive rating | points allowed × 100 / opponent possessions |
| opponent window | the opponent's last 10 games in the same season strictly before the target date |
| league average | all team-games in the trailing 30 days before the target date |
| `opp_pace_rel`, `opp_def_rel` | shrunk ratio to league: (n·team + 5·league) / ((n + 5)·league); 1.0 with no history; def_rel > 1 = weaker defence |

Leakage guards: `build_opponent_context` raises on any opponent line dated on or after the
target; the engine and the service slice strictly before the target date; season
transitions reset the window; early-season samples shrink toward the league average;
missing opponent stats yield an unavailable context and the model reduces to EWMA.

## Model

`ewma_opponent`: projection = EWMA × opp_pace_rel^a × opp_def_rel^b, with per-stat
elasticities fitted by least squares of log((actual + 0.5)/(EWMA + 0.5)) on log pace_rel
and log def_rel over the development season (`ppp fit-opponent-factors`):

| stat | pace elasticity a | defence elasticity b | n | R² |
| --- | --- | --- | --- | --- |
| points | 0.660 | 0.653 | 21,032 | 0.0007 |
| rebounds | 0.891 | 0.260 | 21,032 | 0.0006 |
| assists | 0.567 | 0.721 | 21,032 | 0.0011 |

Signs and ordering are as basketball would predict: rebounds respond mostly to pace,
points and assists to both. The R² values say the opponent explains about 0.1% of the
game-to-game variation around EWMA, which bounds the achievable gain.

## Validation (2024-25, identical 25,158 player-games per stat, `auto` family; fit on 2023-24 only)

| stat | model | MAE | RMSE | bias | NLL | Brier | log loss | ECE | 80% cov |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| points | ewma | 4.683 | 6.127 | −0.100 | 3.089 | 0.2337 | 0.6604 | 0.0306 | 0.798 |
| points | ewma_opponent | 4.677 | 6.119 | −0.101 | 3.087 | 0.2333 | 0.6597 | 0.0309 | 0.799 |
| rebounds | ewma | 1.947 | 2.566 | −0.028 | 2.216 | 0.2011 | 0.5891 | 0.0161 | 0.860 |
| rebounds | ewma_opponent | 1.946 | 2.565 | −0.030 | 2.216 | 0.2010 | 0.5889 | 0.0161 | 0.860 |
| assists | ewma | 1.364 | 1.851 | −0.011 | 1.807 | 0.1848 | 0.5513 | 0.0208 | 0.895 |
| assists | ewma_opponent | 1.361 | 1.846 | −0.011 | 1.806 | 0.1844 | 0.5504 | 0.0207 | 0.895 |

Paired bootstrap (resampling player-games, so the five thresholds per game stay together),
`ewma_opponent` minus `ewma`, 95% CI:

| stat | MAE, all | Brier, all | MAE vs strongest 20% defences | MAE vs weakest 20% defences |
| --- | --- | --- | --- | --- |
| points | **−0.0057 [−0.0090, −0.0027]** | **−0.00033 [−0.00051, −0.00014]** | −0.029 [−0.038, −0.019] | +0.012 [+0.002, +0.021] |
| rebounds | **−0.0014 [−0.0024, −0.0005]** | **−0.00011 [−0.00021, −0.00002]** | −0.005 [−0.007, −0.003] | +0.003 [+0.000, +0.005] |
| assists | **−0.0024 [−0.0033, −0.0016]** | **−0.00037 [−0.00047, −0.00027]** | −0.011 [−0.014, −0.009] | +0.002 [−0.001, +0.005] |

Where the information is: EWMA alone under-forecasts points by 0.52 against the weakest
fifth of defences and over-forecasts by 0.15 against the strongest fifth; the opponent
model cuts those biases to −0.24 and −0.12. The same pattern holds for pace. Against weak
defences the adjustment still leaves half the bias and slightly raises MAE, so the
elasticities are, if anything, too small there.

## Conclusion and recommendation

- Every stat improves on every headline metric, the intervals exclude zero, and calibration
  does not regress. This is a real signal, the first among the contextual features tried.
- The size is 0.006 points, 0.001 rebounds and 0.002 assists of MAE per game. That is not
  a practically meaningful change for a user, so **EWMA remains the default** and
  `ewma_opponent` stays research-only (`ppp predict --model ewma_opponent --opponent XXX`,
  backtests). It is not wired into the dashboard's default path.
- The 2025-26 season was not used; 2026-27 remains untouched. If the opponent model is ever
  promoted, the honest next check is its prospective record, not another historical season.
- Most promising follow-up: opponent effects by player role (bigs vs guards) and positional
  defence, which a single team-level multiplier cannot express.

## Reproduce

```bash
uv run ppp ingest-team-stats --season 2024 --season 2025 --season 2026 --season 2027
uv run ppp backtest --season 2023-24 --models ewma --families auto --no-empirical --no-charts --run-name opp_dev_2023-24
uv run ppp fit-opponent-factors --run data/backtests/opp_dev_2023-24 --out artifacts/opponent_factors.json
uv run ppp backtest --season 2024-25 --models ewma,ewma_opponent --families auto --no-empirical --no-charts --run-name opp_val_2024-25
```
