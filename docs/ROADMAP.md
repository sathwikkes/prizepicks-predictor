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

### Phase 2.5 — Free real NBA data ✅

Done when:
- [x] A verified, free, permitted source of real player game logs: SportsDataverse GitHub
      releases of NBA.com box scores (no key, no payment, MIT-licensed repository).
- [x] `SportsDataverseProvider` joins player box scores to team game logs for dates,
      opponents and scores; caches downloads; idempotent; provenance recorded.
- [x] 2023-24, 2024-25, 2025-26 complete regular seasons and the in-progress 2026-27
      season loaded: 3,778 games, 81,348 player-games, 854 players, 0 rejected.
- [x] `games.season_type` separates regular season from playoffs/play-in; queries and
      predictions default to regular season.
- [x] Data-quality report (`ppp data-quality`, `docs/DATA_QUALITY_REPORT.md`) and a
      300-row cross-check against ESPN box scores with zero stat discrepancies.
- [x] Predictions demonstrated on real players with illustrative lines; still unvalidated.
- [x] Walk-forward evaluation designed (`docs/BACKTESTING_PLAN.md`).

### Phase 3 — Baseline validation ✅ (ML models deferred)

Done when:
- [x] Walk-forward backtest harness per `docs/BACKTESTING_PLAN.md`: develop on 2023-24,
      calibrate on 2024-25, test once on 2025-26 (`ppp backtest`, `ppp calibrate`).
- [x] Metrics per stat: MAE, RMSE, bias, NLL, interval coverage and width, Brier, log loss,
      ECE, reliability tables; strata by season, prior games, minutes, role; bootstrap CIs.
- [x] Platt and isotonic calibration fitted on validation only, evaluated on the held-out season.
- [x] Model versions, config and provenance recorded per run (`run.json`); reports reproducible
      from saved predictions without re-running.
- [x] Forecasting backtests use synthetic, pregame-only, model-independent lines and are
      labelled as such; no wagering simulation exists because no historical lines exist.
- [x] Leakage audit (`docs/LEAKAGE_AUDIT.md`) and licensing risk note (`docs/LICENSING_RISK.md`).
- [ ] Deferred: XGBoost / gradient-boosted models versus the baselines (needs richer features first).
- [ ] Deferred: `model_versions` database table (run.json carries versions for now).

### Phase 3.5 — Forecasting integration and prospective tracking ✅

Done when:
- [x] `predict()` returns raw and calibrated probabilities with the artifact id, method,
      fit seasons and validation status; push probability preserved; documented fallback.
- [x] Versioned calibration artifacts with provenance; validated set (fit 2024-25, held out
      2025-26) is the default; operational 2026-27 refit labelled in-sample only.
- [x] `scheduled_games`, immutable `prediction_snapshots`, `prediction_outcomes`.
- [x] `ppp ingest-schedule`, `ppp forecast`, `ppp evaluate-predictions`, `ppp monitor`.
- [x] First genuine prospective batch saved before tip-off (2026-11-04 games).
- [ ] Daily scheduling of ingest → forecast → evaluate (manual for now).

### Phase 4A — Local Streamlit MVP ✅

Done when:
- [x] Player exploration, prediction analysis, prospective forecasts and model performance
      pages over the existing services; data-status page with staleness warnings.
- [x] Dashboard logic separated (`dashboard/data.py`, `charts.py`) and tested; pages run
      headlessly under `AppTest`.
- [x] Quality fixes: `not_scheduled` outcome status, integer-line calibration warning,
      snapshot statistics (rows vs unique player-game-stat), revision behaviour re-tested.

### Phase 4B — Context features (recommended next)

- Known future back-to-backs and rest from the schedule; opponent pace and defence from
  team box scores; rosters/injuries only with as-of timestamps.
- Re-run the Phase 3 harness unchanged to measure the gain; nothing ships without it.

### Phase 5 — Backend and dashboard

Done when:
- [ ] FastAPI endpoints: health, player search, game logs, predict, model metrics.
- [ ] Streamlit dashboard: player search, trend charts, stat selector, line entry,
      More/Less probabilities with contributing factors.
- [ ] Dashboard reads only from the API.

### Phase 6 — PrizePicks analysis (blocked on licensed data; see docs/LICENSING_RISK.md)

Done when:
- [ ] `projections` table with manual entry (and authorized live import if available).
- [ ] Expected value per pick against verified current Power/Flex payout rules.
- [ ] Multi-pick evaluation that models correlation between legs; pushes and voids handled.
- [ ] Explicit "no statistically supported edge" and "uncertainty too high" outcomes.
- [ ] `prediction_snapshots` and `prediction_outcomes` tables track accuracy over time.

### Phase 7 — Advanced features

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
