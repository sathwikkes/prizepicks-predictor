# Local dashboard (Streamlit)

A local research interface over the existing services. It contains no forecasting logic
of its own: every number comes from `services/`, `prediction/` and the committed backtest
summaries.

## Run

```bash
uv sync                                  # installs the dashboard dependency group
uv run streamlit run src/prizepicks_predictor/dashboard/app.py
```

Open the URL Streamlit prints (default http://localhost:8501). The app reads
`PPP_DATABASE_URL` (default `sqlite:///data/prizepicks.db`) and `PPP_CALIBRATION_DIR`
(default `artifacts/calibration`). Load data first if the database is empty:

```bash
uv run ppp ingest-sdv --season 2025 --season 2026 --season 2027
uv run ppp ingest-schedule --season 2027
```

## Pages

| Page | What it shows | Source |
| --- | --- | --- |
| Overview & data status | latest box-score date, season coverage, schedule status and freshness, calibration artifacts in use, what the saved forecasts represent, staleness warnings | `dashboard.data.data_status` |
| Player exploration | accent-insensitive search, season averages, per-game bar charts with trailing mean for points, rebounds, assists, minutes; season and game-count filters; game log | `services.game_logs` |
| Prediction analysis | player, stat, target date, illustrative threshold → projection, 80% interval, raw and calibrated More/Less/Push, calibration status and artifact, warnings, unavailable context, predictive distribution chart, features used | `prediction.predict` |
| Prospective forecasts | saved snapshots filtered by date, game, player, stat, status; prospective vs unverified; statuses pending / evaluated / did_not_play / voided / not_scheduled / missing_result | `services.forecast`, `services.evaluation` |
| Model performance | Phase 3 backtests (comparison tables, MAE and Brier bars, reliability curves, calibration on the held-out run, full summary) in one tab; prospective outcomes from `ppp monitor` in a separate tab | `docs/backtests/*`, `services.monitoring` |

## Reading the labels

* **Illustrative threshold / synthetic line**: a number you typed or the trailing-10-game
  median. Not a PrizePicks line. Probabilities against it measure forecasting skill, not
  betting value.
* **prospective**: the snapshot's cutoff is before the game's known tip-off. **unverified**:
  no tip-off was recorded (manual or replay targets); treat as historical replay.
* **expected participant**: inferred from recent appearances; not a confirmed active
  player. Inactive players evaluate as `did_not_play`.
* **not_scheduled** vs **missing_result**: the first means the schedule is loaded and the
  player's team had no game that day; the second means the game is not in the box-score
  data yet (or no schedule exists for that date, so the two cannot be told apart).
* **calibration_integer_line_unvalidated**: the calibrator was fitted on half-point
  lines; at integer lines it is applied to P(more | no push) without having been
  validated for that case. The push probability itself is never calibrated.

## Architecture

```
dashboard/app.py       st.navigation entry point
dashboard/pages.py     one function per page; Streamlit calls only
dashboard/common.py    cached engine/session, disclaimer
dashboard/data.py      read functions returning dataclasses / Pydantic models (no Streamlit)
dashboard/charts.py    Plotly figures as pure functions
```

`data.py` and `charts.py` are unit-tested directly; the pages are exercised headlessly with
`streamlit.testing.v1.AppTest` against a temporary database.

## Limits

* Local only; no authentication, no deployment, no scheduling.
* The 2026-27 box scores lag live games by about a day, so prospective outcomes appear
  the day after games.
* The project is non-commercial research under the source data terms
  (`docs/LICENSING_RISK.md`). The dashboard shows no market lines and recommends no picks.
