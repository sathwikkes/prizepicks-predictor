# PrizePicks Predictor

NBA player prop analytics platform: statistical forecasting, probability calibration, and
expected-value analysis for PrizePicks More/Less projections.

> This is a research and simulation tool. It does not place wagers, and no model in this
> repository should be assumed profitable without reliable out-of-sample evidence.

## Status

**Phases 1, 2 and 2.5 are implemented.** The repository has a SQLite schema, a
provider-agnostic ingestion framework, four interpretable baseline models, count-aware
predictive distributions, a CLI that turns a player, stat, line and game date into
More/Less/Push probabilities, and a free real-data pipeline that loads NBA.com box scores
for 2023-24 through the current season from SportsDataverse's public GitHub releases.

Probabilities are **unvalidated**: the models now run on real history but have not yet been
backtested against real outcomes. See [docs/ROADMAP.md](docs/ROADMAP.md),
[docs/PREDICTION_METHODOLOGY.md](docs/PREDICTION_METHODOLOGY.md),
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md), [docs/DATA_QUALITY_REPORT.md](docs/DATA_QUALITY_REPORT.md)
and [docs/BACKTESTING_PLAN.md](docs/BACKTESTING_PLAN.md).

## Setup

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
cp .env.example .env   # optional; defaults work for local development
```

## Quickstart with real NBA data

```bash
# 1. Create the SQLite database (default: data/prizepicks.db)
uv run ppp init-db

# 2. Download and load real regular-season box scores (about 20 MB, cached under data/raw/).
#    Seasons use the END year: 2025 = 2024-25. Takes about a minute per full season.
uv run ppp ingest-sdv --season 2024 --season 2025 --season 2026 --season 2027

# 3. Check what you have
uv run ppp data-quality
uv run ppp players --search jokic          # accent-insensitive
uv run ppp game-logs "Nikola Jokic" --limit 5

# 4. Predict against an illustrative line, using only games before the date
uv run ppp predict --player-id <id> --stat points --line 27.5 --date 2025-03-01
```

`ingest-sdv` is idempotent: re-running it downloads nothing (files are cached) and reports
`unchanged=…`. Add `--season-types regular,playoffs` for playoff games or `--include-dnp`
to store did-not-play rows as 0-minute games.

## Quickstart with the synthetic fixture (software check only)

```bash
uv run ppp init-db --database-url sqlite:///data/synthetic.db
uv run ppp --database-url sqlite:///data/synthetic.db ingest-csv data/samples/synthetic_game_logs.csv --source csv_fixture
uv run ppp --database-url sqlite:///data/synthetic.db predict --player-id 1 --stat points --line 24.5 --date 2024-12-01
```

The fixture has fictional players and random stats. Keep it in a separate database from
real data so no evaluation ever mixes the two.

### Prediction options

| Flag | Values | Notes |
| --- | --- | --- |
| `--stat` | `points`, `rebounds`, `assists` | |
| `--line` | multiple of 0.5 | integer lines report a push probability |
| `--date` | `YYYY-MM-DD` | only games strictly before this date are used |
| `--model` | `season_average`, `rolling_5`, `rolling_10`, `ewma` (default), `minutes_adjusted` | |
| `--distribution` | `auto` (default), `poisson`, `negative_binomial`, `normal`, `empirical` | `auto` picks Negative Binomial when overdispersed |

Exit codes: 0 ok, 1 unknown player, 2 invalid request, 3 insufficient data, 4 database failure.

### Loading your own CSV

Required columns: `game_date` (ISO), `player_id`, `player_name`, `team`, `opponent`,
`home_away` (`home`/`away`), `minutes` (`34:30` or `34.5`), `points`, `rebounds`, `assists`.
Optional: `source_game_id`, `season`, `steals`, `blocks`, `turnovers`, `fg3m`, `started`,
`team_score`, `opponent_score`. Use `--source <label>` to tag the provenance of the data.

## Data

Real data comes from SportsDataverse's public GitHub releases of NBA.com box scores
(`ingestion/sportsdataverse.py`). No API key or subscription is needed. The release
tagged `nba_stats_player_game_logs` actually holds *team* game logs; the adapter joins it
with `nba_stats_player_boxscores` to obtain dates, opponents and scores. Details, verified
freshness and limitations are in `docs/DATA_SOURCES.md`; the import's quality report is
`docs/DATA_QUALITY_REPORT.md`.

## Development

```bash
uv run pytest                 # tests
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # strict type checking on src/
```

## Project layout

```
src/prizepicks_predictor/
  config.py          # pydantic-settings, PPP_* environment variables
  db/                # SQLAlchemy models and engine/session helpers
  schemas/           # Pydantic contracts (NormalizedGameLog, GameLogRow)
  ingestion/         # provider adapters (csv, sportsdataverse, balldontlie) + idempotent loader
  services/          # query layer used by the CLI and, later, the API
  prediction/        # features -> baseline models -> distributions -> predict()
  cli.py             # `ppp` command-line entry point
tests/               # pytest suite
data/samples/        # committed synthetic fixtures (labeled as such)
data/raw/            # downloaded real data cache (gitignored)
scripts/             # fixture generator
docs/                # roadmap, architecture notes, data-source evaluation
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PPP_DATABASE_URL` | `sqlite:///data/prizepicks.db` | SQLAlchemy database URL |
| `PPP_BALLDONTLIE_API_KEY` | unset | Enables the live BALLDONTLIE adapter |
| `PPP_LOG_LEVEL` | `INFO` | CLI log verbosity |

Never commit `.env` or any API key.
