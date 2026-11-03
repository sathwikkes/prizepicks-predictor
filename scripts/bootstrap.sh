#!/usr/bin/env bash
# Local bootstrap for macOS / Linux. Idempotent: safe to re-run after an interrupted download.
#   scripts/bootstrap.sh               setup + data + readiness check + dashboard smoke test
#   scripts/bootstrap.sh --setup-only  install + verify CLI + initialise the database only
set -euo pipefail
cd "$(dirname "$0")/.."

SEASONS="${SEASONS:-2024 2025 2026 2027}"      # END years: 2025 = 2024-25
CURRENT_SEASON="${CURRENT_SEASON:-2027}"
SETUP_ONLY=0
if [[ "${1:-}" == "--setup-only" ]]; then SETUP_ONLY=1; fi

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

step "Checking uv"
if ! command -v uv >/dev/null 2>&1; then
  fail "uv is not installed. Install with:  brew install uv   (or: curl -LsSf https://astral.sh/uv/install.sh | sh)"
fi
uv --version

step "Checking Python (3.12+; uv installs one if missing)"
if ! uv python find '>=3.12' >/dev/null 2>&1; then
  echo "No Python 3.12+ found; letting uv install one"
  uv python install 3.12
fi
uv python find '>=3.12'

step "Installing dependencies (uv sync)"
uv sync

step "Verifying the CLI"
uv run ppp --help >/dev/null || fail "uv run ppp --help failed"
echo "ppp CLI ok"

if [[ ! -f .env && -f .env.example ]]; then
  cp .env.example .env
  echo "created .env from .env.example (default database: sqlite:///data/prizepicks.db)"
fi

step "Initialising the database (idempotent)"
uv run ppp init-db

if [[ "$SETUP_ONLY" == "1" ]]; then
  step "Setup complete. Next: make data   (or scripts/bootstrap.sh for everything)"
  exit 0
fi

step "Downloading and importing box scores for seasons: $SEASONS"
echo "Files are cached under data/raw/sportsdataverse; an interrupted download leaves a .part file that is replaced on retry."
args=()
for s in $SEASONS; do args+=(--season "$s"); done
uv run ppp ingest-sdv "${args[@]}"

step "Loading the $CURRENT_SEASON schedule"
uv run ppp ingest-schedule --season "$CURRENT_SEASON"

step "Loading team box-score totals (opponent context)"
uv run ppp ingest-team-stats "${args[@]}"

step "Data quality"
uv run ppp data-quality

step "Readiness check"
uv run ppp check

step "Checking the dashboard starts (headless, 25 s budget)"
port="${PPP_SMOKE_PORT:-8599}"
log="${TMPDIR:-/tmp}/ppp-streamlit-smoke.log"
uv run streamlit run src/prizepicks_predictor/dashboard/app.py --server.headless true --server.port "$port" --browser.gatherUsageStats false >"$log" 2>&1 &
pid=$!
ok=0
for _ in $(seq 1 25); do
  sleep 1
  if curl -sS -m 2 "http://localhost:$port/_stcore/health" 2>/dev/null | grep -q ok; then ok=1; break; fi
done
kill "$pid" >/dev/null 2>&1 || true
if [[ "$ok" == "1" ]]; then
  echo "dashboard health check ok"
else
  fail "dashboard did not answer on port $port; see $log"
fi

step "Done. Launch with:  make run"
