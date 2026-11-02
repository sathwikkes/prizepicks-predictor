# PrizePicks Predictor — Development Roadmap

An NBA player prop analytics platform that estimates calibrated More/Less probabilities for
PrizePicks projections and identifies where a statistically supported edge may exist.
Initial scope: points, rebounds, assists. Architected for more stat types and sports later.

## Guiding principles

- **Calibration over accuracy.** A model is useful when its probabilities are calibrated at
  the line, not when it predicts a player's average well.
- **Backtest before you trust.** Chronological splits only. Every feature must be available
  before tip-off. Nothing is called profitable without out-of-sample evidence.
- **Simple baselines first.** Beat season and rolling averages before adding complexity.
- **Thin UI, thick API.** All logic lives behind services and the FastAPI layer.
- **Authorized data only.** No scraping of sites that forbid it, no private PrizePicks
  endpoints. Synthetic fixtures are always labeled as synthetic.

## Stack

| Layer | Tooling |
| --- | --- |
| Language / packaging | Python 3.12+, uv, `pyproject.toml` |
| Data | httpx, Pydantic v2, SQLAlchemy 2, SQLite (Postgres-ready); Polars/Pandas/NumPy from Phase 2 |
| Modeling | SciPy distributions, scikit-learn, XGBoost, probability calibration |
| Backend | FastAPI, Pydantic |
| Frontend | Streamlit + Plotly (MVP); React/Next.js + Tailwind later |
| Quality | pytest, Ruff, mypy (strict) |

Dependencies are added only when the current phase needs them.

## Phases

### Phase 1 — Repository foundation and data infrastructure ✅

Done when:
- [x] uv project with Ruff, mypy, pytest configured; `.env.example`; README setup instructions.
- [x] Normalized SQLite schema: seasons, teams, players, games, player_game_stats,
      external_ids, ingestion_runs. Unique constraints on natural keys; FKs enforced.
- [x] Provider adapter protocol with a working CSV adapter and a BALLDONTLIE adapter
      (mock-tested; live access requires a paid key).
- [x] Idempotent loader with provenance: re-import reports inserted/updated/unchanged/rejected.
- [x] `ppp game-logs <player>` returns date, team, opponent, minutes, pts, reb, ast.
- [x] Automated tests for init, validation, insertion, duplicates, retrieval, error handling.
- [x] Data-source evaluation documented (`docs/DATA_SOURCES.md`).

### Phase 2 — Baseline prediction engine

Done when:
- [ ] Feature builder computes, strictly from games before a target date: season avg,
      last-5 / last-10 avg, EWMA, per-minute rates, std dev, home/away splits, rest days.
- [ ] Baselines implemented for PTS/REB/AST: season mean, rolling mean, EWMA, minutes-adjusted.
- [ ] Distribution models produce a full predictive distribution (Normal and Negative
      Binomial candidates, selected empirically), not just a point estimate.
- [ ] `predict(player, stat, line, game_date)` returns predicted total, P(more), P(less),
      P(push) for integer lines, uncertainty, contributing factors, model version, timestamp.
- [ ] Prediction is reproducible: same inputs and model version give identical output.
- [ ] Tests cover the no-leakage guarantee (target game excluded from features).

### Phase 3 — Model training and validation

Done when:
- [ ] Walk-forward backtest harness over the historical dataset (chronological folds).
- [ ] Metrics reported per stat type: MAE, RMSE, Brier, log loss, calibration curves, hit rate.
- [ ] XGBoost / gradient-boosted models trained and compared against every baseline.
- [ ] Probability calibration (Platt / isotonic) evaluated on held-out folds.
- [ ] `model_versions` table and experiment log; each backtest report is reproducible.
- [ ] Forecasting backtests are clearly distinguished from wagering simulations, which
      require timestamped historical lines we do not yet have.

### Phase 4 — Backend and dashboard

Done when:
- [ ] FastAPI endpoints: health, player search, game logs, predict, model metrics.
- [ ] Streamlit dashboard: player search, trend charts, stat selector, line entry,
      More/Less probabilities with contributing factors.
- [ ] Dashboard reads only from the API.

### Phase 5 — PrizePicks analysis

Done when:
- [ ] `projections` table with manual entry (and authorized live import if available).
- [ ] Expected value per pick against verified current Power/Flex payout rules.
- [ ] Multi-pick evaluation that models correlation between legs; pushes and voids handled.
- [ ] Explicit "no statistically supported edge" and "uncertainty too high" outcomes.
- [ ] `prediction_snapshots` and `prediction_outcomes` tables track accuracy over time.

### Phase 6 — Advanced features

- AI-assisted matchup research and injury-report interpretation.
- Automated schedule and injury updates from an authorized source.
- Additional stat categories (3PM, PRA, etc.) and additional sports.
- Production frontend (React/Next.js), containerization, deployment and monitoring.

## Out of scope

- Automated wagering or any interaction with PrizePicks accounts.
- Promises or claims of profitability.

## Disclaimer

Research and educational use only. Prop betting involves financial risk; model outputs
are probabilistic estimates, not guarantees.
