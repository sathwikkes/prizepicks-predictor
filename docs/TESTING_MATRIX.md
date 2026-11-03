# Testing matrix

See `docs/END_TO_END_QA_REPORT.md` §1 for the feature-to-test mapping and verification
status. Test locations:

| Area | Directory |
| --- | --- |
| Database, ingestion, query, CLI (Phase 1–2.5) | `tests/test_*.py` |
| Features, models, distributions, service, leakage (Phase 2) | `tests/prediction/` |
| Walk-forward harness, metrics, calibration fitting (Phase 3) | `tests/backtest/` |
| Calibration artifacts, forecast, evaluation, schedule, monitoring (Phase 3.5) | `tests/forecasting/` |
| Dashboard data layer, charts, headless pages, stabilization regressions (Phase 4A/4A.1) | `tests/dashboard/` |

Run everything with `uv run pytest`; the dashboard tests use `streamlit.testing.v1.AppTest`
against temporary databases and never touch `data/prizepicks.db`.
