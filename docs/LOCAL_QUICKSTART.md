# Local quick start (macOS)

Everything runs locally against a SQLite file. No API keys, accounts or paid services.

## 1. Prerequisites

- `uv` (`brew install uv`, or the installer at https://astral.sh/uv). uv installs a suitable
  Python (3.12+) by itself if none is present.
- Git.

## 2. Install, populate, check

```bash
git clone https://github.com/sathwikkes/prizepicks-predictor.git
cd prizepicks-predictor
make bootstrap          # setup + data + check + headless dashboard smoke test (about 20 MB of downloads)
```

Or step by step:

```bash
make setup              # uv sync, verify `ppp`, create .env from .env.example, initialise the database
make data               # import 2023-24 .. 2026-27 box scores + the 2026-27 schedule, print data quality
make check              # readiness: schema, triggers, integrity, seasons, schedule, artifacts, smoke prediction
make run                # Streamlit at http://localhost:8501
```

`make data` is idempotent: re-running imports nothing new and never duplicates rows. An
interrupted download leaves a `.part` file under `data/raw/sportsdataverse/` that is
replaced on the next run. Seasons use the END year (2025 = 2024-25); change the set with
`make data SEASONS="2026 2027"`.

## 3. Daily use

```bash
make refresh            # re-download the current season's box scores and schedule, then check
uv run ppp forecast --date YYYY-MM-DD --stat all --save   # pregame forecasts (refuses started games)
uv run ppp evaluate-predictions --through YYYY-MM-DD      # after `make refresh` picks up the box scores
uv run ppp monitor
make run
```

The CLI and the dashboard read the same file: `PPP_DATABASE_URL` in `.env` (default
`sqlite:///data/prizepicks.db`). Calibration artifacts are committed under
`artifacts/calibration/` and load automatically; `make check` confirms it.

## 4. Backups

```bash
make backup                                            # data/backups/prizepicks-<UTC>.sqlite (SQLite online backup API)
make restore FILE=data/backups/<file>.sqlite           # refuses to overwrite a populated database
make restore FILE=data/backups/<file>.sqlite FORCE=1   # replaces it, keeping a pre-restore safety copy next to it
```

Backups preserve every table including prediction snapshots; `restore` re-runs `init-db`
so the immutability triggers exist. `data/` is gitignored: never commit databases or raw
downloads.

## 5. Moving from the Claude cloud session to this Mac

The cloud session's `data/prizepicks.db` is **not** in GitHub, and the Mac cannot read the
cloud container's files. What is and is not reconstructible:

| Content | Reconstructible on the Mac? | How |
| --- | --- | --- |
| Box scores 2023-24 .. 2026-27 and the schedule | yes, from public data | `make data` |
| Calibration artifacts, backtest summaries | yes, committed | `git clone` |
| Full Phase 3 per-prediction backtest files (~150 MB) | yes, recomputable | `uv run ppp backtest ...` (about 2.5 min per season) |
| **Prospective prediction snapshots** (1,191 saved before the 2026-11-04 games, plus the 36-row March replay) | **no**: a pregame forecast cannot be honestly re-created after the games | import the committed export: `make import-snapshots FILE=artifacts/snapshots/<file>.csv.gz` |

The snapshot export (`artifacts/snapshots/*.csv.gz`, a few hundred KB) is keyed by NBA
player ids and ESPN game ids, so it links correctly after `make data` even though internal
row ids differ between databases. Import inserts only snapshots not already present and
never updates anything.

Supported transfer options, in order of preference:

1. `make data`, then `make import-snapshots FILE=artifacts/snapshots/<latest>.csv.gz`.
   No cloud file access is needed.
2. If you ever obtain the cloud database file itself, `make restore FILE=<that file>`.

Direct file download from the cloud container to the Mac is not something this repository
can provide; the committed snapshot export is the supported path.

## 6. Tests

```bash
make test               # pytest, ruff check, ruff format --check, mypy
```
