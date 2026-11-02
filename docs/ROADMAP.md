# PrizePicks Predictor — Development Roadmap

A Python application that predicts NBA player prop outcomes (points, rebounds, assists, threes, PRA, etc.) and surfaces the highest-edge picks against PrizePicks lines.

## Guiding Principles

- **Reproducible first.** Every prediction must be traceable to a dataset snapshot, a model version, and a feature set.
- **Backtest before you trust.** No model ships to the dashboard without a documented out-of-sample track record.
- **Simple models, strong baselines.** Beat the "player's season average" baseline before adding complexity.
- **Thin UI, thick API.** All logic lives behind the FastAPI service; the Streamlit dashboard only reads from it.

## Planned Python Stack

| Layer | Tooling |
| --- | --- |
| Language / packaging | Python 3.12, `uv` for dependency and venv management, `pyproject.toml` |
| Data ingestion | `nba_api`, `httpx`, `pandas`, `pyarrow` |
| Storage | DuckDB (local analytics) with Parquet files; SQLite for app state |
| Feature engineering | `pandas`, `polars` (optional for speed), `numpy` |
| Modeling | `scikit-learn`, `statsmodels`, `lightgbm`, `scipy` |
| Backtesting | Custom harness on `pandas`; `pytest` for regression tests |
| Backend | FastAPI, `pydantic` v2, `uvicorn` |
| Dashboard | Streamlit, `plotly` |
| Quality | `ruff`, `mypy`, `pytest`, `pre-commit` |
| Orchestration | Simple CLI scripts first (`typer`); scheduled jobs via cron or GitHub Actions |

## Phases

### Phase 0 — Project Scaffolding
- Repository layout: `src/prizepicks_predictor/`, `tests/`, `docs/`, `data/` (gitignored).
- `pyproject.toml`, `ruff` and `mypy` configuration, `pre-commit` hooks.
- Settings module (`pydantic-settings`) for API keys, paths, and environment selection.
- CI workflow: lint, type-check, and test on every push.

### Phase 1 — Data Ingestion
- Pull player game logs, box scores, team schedules, and injury reports via `nba_api`.
- Scrape or ingest PrizePicks projection lines (respecting rate limits and terms of service); store raw snapshots with timestamps.
- Normalize into DuckDB tables: `players`, `games`, `player_game_stats`, `prop_lines`, `injuries`.
- Incremental daily refresh job with idempotent upserts and data quality checks (row counts, null rates, duplicate keys).

### Phase 2 — Feature Engineering
- Rolling windows (last 5 / 10 / 20 games, season-to-date) for each stat.
- Context features: home/away, rest days, back-to-back, opponent defensive rating, pace, projected minutes, teammate availability.
- Usage-rate and minutes-share features derived from recent lineups.
- Point-in-time correctness: every feature computed only from data available before tip-off.

### Phase 3 — Statistical Models
- **Baselines:** season average, exponentially weighted moving average.
- **Distributional models:** Poisson / Negative Binomial for counting stats; Gaussian for minutes-driven stats; produce full distributions, not just point estimates.
- **Regression models:** regularized linear models and LightGBM for the mean, with quantile or variance models for spread.
- **Over/under probability:** convert the predicted distribution into `P(stat > line)` and an implied edge versus the PrizePicks payout structure.
- Model registry: versioned artifacts with training window, feature list, and evaluation metrics.

### Phase 4 — Backtesting
- Walk-forward backtest harness: train on past games, predict the next slate, roll forward day by day.
- Metrics: hit rate by prop type, calibration (reliability curves, Brier score), log loss, ROI under flat-stake and Kelly staking.
- Slice analysis: by player tier, stat type, line size, days of rest, and season segment.
- Leakage audit: assert that no feature uses post-game information.
- Store backtest reports under `docs/backtests/` for each model version.

### Phase 5 — FastAPI Backend
- Endpoints:
  - `GET /health`
  - `GET /slate/{date}` — today's available props with predictions and edge.
  - `GET /players/{player_id}/props` — history and model outputs for a player.
  - `GET /models` — registered model versions and metrics.
  - `POST /refresh` — trigger an ingestion and prediction run (protected).
- Pydantic response schemas, OpenAPI docs, structured logging, and request-level caching.
- Background scheduler for the daily refresh.

### Phase 6 — Streamlit Dashboard
- **Slate view:** sortable table of today's props with predicted probability, edge, and confidence tier.
- **Player view:** game-log trend charts, rolling averages, and distribution overlay versus the current line.
- **Model view:** calibration plots and backtest ROI by version.
- Filters for stat type, team, minimum edge, and injury status.
- Reads exclusively from the FastAPI service.

### Phase 7 — Hardening and Operations
- Containerize API and dashboard with Docker Compose.
- Alerting when ingestion fails or model drift exceeds thresholds.
- Documented runbooks for refresh, retraining, and rollback.

## Milestones

| Milestone | Definition of done |
| --- | --- |
| M1 | Daily game logs and prop lines land in DuckDB automatically. |
| M2 | Baseline and distributional models beat the season-average baseline in walk-forward backtest. |
| M3 | FastAPI serves a daily slate with predictions and edge. |
| M4 | Streamlit dashboard is usable end-to-end for a live slate. |
| M5 | Full-season backtest report published with calibration and ROI. |

## Out of Scope (for now)

- Automated bet placement or any interaction with PrizePicks accounts.
- Sports other than the NBA.
- Deep learning models until classical baselines are exhausted.

## Disclaimer

This project is for research and educational purposes. Prop betting involves financial risk; predictions are probabilistic and not guarantees.
