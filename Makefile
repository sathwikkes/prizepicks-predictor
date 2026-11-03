# PrizePicks Predictor — local workflow (macOS / Linux). All targets use the project's uv environment.
# Seasons use the END year: 2025 = 2024-25. Override: make data SEASONS="2026 2027"
SHELL := /bin/bash
UV ?= uv
SEASONS ?= 2024 2025 2026 2027
CURRENT_SEASON ?= 2027
BACKUP_DIR ?= data/backups
SNAPSHOT_DIR ?= artifacts/snapshots

.PHONY: help setup data refresh run check test backup restore export-snapshots import-snapshots bootstrap

help:
	@echo "make setup              install dependencies, verify the CLI, initialise the database"
	@echo "make data               download + import historical seasons ($(SEASONS)) and the current schedule"
	@echo "make refresh            re-download the current season ($(CURRENT_SEASON)) box scores and schedule"
	@echo "make run                launch the Streamlit dashboard"
	@echo "make check              readiness checks (schema, data, schedule, artifacts, smoke prediction)"
	@echo "make test               pytest + ruff + mypy"
	@echo "make backup             consistent SQLite copy into $(BACKUP_DIR)/"
	@echo "make restore FILE=...   replace the database with a backup (add FORCE=1 if populated)"
	@echo "make export-snapshots   write prediction snapshots to $(SNAPSHOT_DIR)/snapshots-<date>.csv.gz"
	@echo "make import-snapshots FILE=...   insert exported snapshots not already present"
	@echo "make bootstrap          setup + data + check"

setup:
	@scripts/bootstrap.sh --setup-only

data:
	$(UV) run ppp ingest-sdv $(foreach s,$(SEASONS),--season $(s))
	$(UV) run ppp ingest-schedule --season $(CURRENT_SEASON)
	$(UV) run ppp data-quality

refresh:
	$(UV) run ppp ingest-sdv --season $(CURRENT_SEASON) --force-download
	$(UV) run ppp ingest-schedule --season $(CURRENT_SEASON) --force-download
	$(UV) run ppp check

run:
	$(UV) run streamlit run src/prizepicks_predictor/dashboard/app.py

check:
	$(UV) run ppp check

test:
	$(UV) run pytest
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

backup:
	$(UV) run ppp backup

restore:
	@test -n "$(FILE)" || (echo "usage: make restore FILE=path/to/backup.sqlite [FORCE=1]" && exit 2)
	$(UV) run ppp restore --from "$(FILE)" $(if $(FORCE),--force,)

export-snapshots:
	$(UV) run ppp export-snapshots --out "$(SNAPSHOT_DIR)/snapshots-$$(date -u +%Y%m%d).csv.gz"

import-snapshots:
	@test -n "$(FILE)" || (echo "usage: make import-snapshots FILE=artifacts/snapshots/<file>.csv.gz" && exit 2)
	$(UV) run ppp import-snapshots --from "$(FILE)"

bootstrap:
	@scripts/bootstrap.sh
