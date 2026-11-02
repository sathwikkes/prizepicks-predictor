# PrizePicks Predictor

NBA player prop analytics platform: statistical forecasting, probability calibration, and
expected-value analysis for PrizePicks More/Less projections.

> This is a research and simulation tool. It does not place wagers, and no model in this
> repository should be assumed profitable without reliable out-of-sample evidence.

## Status

**Phase 1 (data foundation) is implemented.** The repository has a SQLite schema, a
provider-agnostic ingestion framework, a working CSV ingestion path with a clearly labeled
synthetic fixture, a BALLDONTLIE adapter (unit-tested against a mocked API only), and a CLI
that queries a player's historical game logs. See [docs/ROADMAP.md](docs/ROADMAP.md) for the
phase plan and [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) for the provider evaluation.

## Setup

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
cp .env.example .env   # optional; defaults work for local development
```

## Quickstart

```bash
# 1. Create the SQLite database (default: data/prizepicks.db)
uv run ppp init-db

# 2. Load the synthetic fixture (fictional players, random stats; NOT real NBA data)
uv run ppp ingest-csv data/samples/synthetic_game_logs.csv --source csv_fixture

# 3. Query
uv run ppp players --search riv
uv run ppp game-logs "Alex Rivera" --limit 5
uv run ppp runs
```

Re-running the ingest command is safe: it reports `unchanged=72` and inserts nothing.

### Loading your own CSV

Required columns: `game_date` (ISO), `player_id`, `player_name`, `team`, `opponent`,
`home_away` (`home`/`away`), `minutes` (`34:30` or `34.5`), `points`, `rebounds`, `assists`.
Optional: `source_game_id`, `season`, `steals`, `blocks`, `turnovers`, `fg3m`, `started`,
`team_score`, `opponent_score`. Use `--source <label>` to tag the provenance of the data.

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
  ingestion/         # provider adapters (csv, balldontlie) + idempotent loader
  services/          # query layer used by the CLI and, later, the API
  cli.py             # `ppp` command-line entry point
tests/               # pytest suite
data/samples/        # committed synthetic fixtures (labeled as such)
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
