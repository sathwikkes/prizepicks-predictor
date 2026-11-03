"""Local readiness checks: is this installation able to search, predict, forecast and serve?

Each check returns OK, WARN or FAIL with a one-line detail and, when actionable, the command
that fixes it. ``ppp check`` prints them and exits non-zero on any FAIL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import (
    Game,
    PlayerGameStat,
    PredictionSnapshot,
    ScheduledGame,
    Season,
)
from prizepicks_predictor.prediction import PredictionRequest, StatType, predict
from prizepicks_predictor.prediction.calibrators import CalibratorRegistry
from prizepicks_predictor.prediction.service import InsufficientDataError
from prizepicks_predictor.services.game_logs import PlayerNotFoundError

REQUIRED_TABLES = {
    "seasons",
    "teams",
    "players",
    "games",
    "player_game_stats",
    "external_ids",
    "ingestion_runs",
    "scheduled_games",
    "prediction_snapshots",
    "prediction_outcomes",
}
REQUIRED_TRIGGERS = {"trg_prediction_snapshots_no_update", "trg_prediction_snapshots_no_delete"}
#: Completed seasons expected to be fully loaded (start year -> regular-season games).
COMPLETE_SEASONS = {2023: 1230, 2024: 1230, 2025: 1230}


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str
    fix: str = ""


def _ok(name: str, detail: str) -> Check:
    return Check(name, "OK", detail)


def _warn(name: str, detail: str, fix: str = "") -> Check:
    return Check(name, "WARN", detail, fix)


def _fail(name: str, detail: str, fix: str = "") -> Check:
    return Check(name, "FAIL", detail, fix)


def run_checks(
    session: Session,
    *,
    calibration_dir: Path,
    backtest_dir: Path = Path("docs/backtests"),
    current_season_start: int | None = None,
    now: datetime | None = None,
) -> list[Check]:
    now_utc = (now or datetime.now(UTC)).replace(tzinfo=None)
    today = now_utc.date()
    season_now = (
        current_season_start
        if current_season_start is not None
        else (today.year if today.month >= 10 else today.year - 1)
    )
    checks: list[Check] = []

    # --- schema ---------------------------------------------------------
    try:
        names = {
            r[0] for r in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        triggers = {
            r[0]
            for r in session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
        }
    except SQLAlchemyError as exc:
        return [
            _fail(
                "database",
                f"cannot read schema: {exc}",
                "check PPP_DATABASE_URL; run `uv run ppp init-db`",
            )
        ]
    missing = REQUIRED_TABLES - names
    checks.append(
        _ok("schema", f"{len(REQUIRED_TABLES)} tables present")
        if not missing
        else _fail("schema", f"missing tables {sorted(missing)}", "uv run ppp init-db")
    )
    checks.append(
        _ok("immutability triggers", "installed")
        if triggers >= REQUIRED_TRIGGERS
        else _fail("immutability triggers", "missing", "uv run ppp init-db")
    )
    integrity = session.execute(text("PRAGMA integrity_check")).scalar()
    checks.append(
        _ok("sqlite integrity", str(integrity))
        if integrity == "ok"
        else _fail(
            "sqlite integrity",
            str(integrity),
            "restore from a backup: uv run ppp restore --from <file>",
        )
    )

    # --- game logs --------------------------------------------------------
    coverage = {
        int(sy): (int(games), int(rows))
        for sy, games, rows in session.execute(
            select(
                Season.start_year, func.count(func.distinct(Game.id)), func.count(PlayerGameStat.id)
            )
            .join(Game, Game.season_id == Season.id)
            .outerjoin(PlayerGameStat, PlayerGameStat.game_id == Game.id)
            .where(Game.season_type == "regular")
            .group_by(Season.start_year)
        )
    }
    if not coverage:
        checks.append(_fail("game logs", "no games loaded", "make data"))
    for sy, expected in COMPLETE_SEASONS.items():
        got = coverage.get(sy)
        label = f"season {sy}-{(sy + 1) % 100:02d}"
        if got is None:
            checks.append(_fail(label, "not loaded", f"uv run ppp ingest-sdv --season {sy + 1}"))
        elif got[0] < expected:
            checks.append(
                _warn(
                    label,
                    f"{got[0]}/{expected} games",
                    f"uv run ppp ingest-sdv --season {sy + 1} --force-download",
                )
            )
        else:
            checks.append(_ok(label, f"{got[0]} games, {got[1]} player-games"))
    cur = coverage.get(season_now)
    cur_label = f"season {season_now}-{(season_now + 1) % 100:02d} (current)"
    if cur is None:
        checks.append(
            _warn(cur_label, "not loaded yet", f"uv run ppp ingest-sdv --season {season_now + 1}")
        )
    else:
        checks.append(_ok(cur_label, f"{cur[0]} games, {cur[1]} player-games"))

    latest = session.scalar(select(func.max(Game.game_date)))
    last_final = session.scalar(
        select(func.max(ScheduledGame.game_date)).where(ScheduledGame.status == "final")
    )
    if latest is None:
        checks.append(_fail("latest box score", "none", "make data"))
    elif last_final is not None and last_final > latest:
        checks.append(
            _warn(
                "latest box score",
                f"{latest}; schedule shows finals through {last_final}",
                f"uv run ppp ingest-sdv --season {season_now + 1} --force-download",
            )
        )
    else:
        checks.append(_ok("latest box score", str(latest)))

    # --- schedule ---------------------------------------------------------
    sched_count = session.scalar(select(func.count()).select_from(ScheduledGame)) or 0
    loaded_at = session.scalar(select(func.max(ScheduledGame.loaded_at)))
    next_date = session.scalar(
        select(func.min(ScheduledGame.game_date)).where(
            ScheduledGame.status == "scheduled", ScheduledGame.game_date >= today
        )
    )
    if sched_count == 0:
        checks.append(
            _warn(
                "schedule",
                "not loaded (forecasts unavailable)",
                f"uv run ppp ingest-schedule --season {season_now + 1}",
            )
        )
    elif loaded_at is not None and (now_utc - loaded_at).days >= 2:
        checks.append(
            _warn(
                "schedule",
                f"{sched_count} games, loaded {loaded_at:%Y-%m-%d}; next {next_date}",
                f"uv run ppp ingest-schedule --season {season_now + 1} --force-download",
            )
        )
    else:
        checks.append(_ok("schedule", f"{sched_count} games; next game date {next_date}"))

    # --- artifacts --------------------------------------------------------
    registry = CalibratorRegistry.load(calibration_dir)
    validated = [a for a in registry.artifacts if a.validation_status == "held_out_validated"]
    if len(registry) == 0:
        checks.append(
            _warn(
                "calibration artifacts",
                f"none in {calibration_dir}",
                "git checkout artifacts/ or uv run ppp fit-calibrators ...",
            )
        )
    else:
        checks.append(
            _ok(
                "calibration artifacts",
                f"{len(registry)} loaded ({len(validated)} held-out validated)",
            )
        )
    runs = (
        [p.name for p in backtest_dir.iterdir() if (p / "comparison.csv").is_file()]
        if backtest_dir.is_dir()
        else []
    )
    checks.append(
        _ok("backtest reports", ", ".join(sorted(runs)))
        if runs
        else _warn("backtest reports", f"none under {backtest_dir}", "git checkout docs/backtests")
    )

    # --- snapshots --------------------------------------------------------
    snaps = session.scalar(select(func.count()).select_from(PredictionSnapshot)) or 0
    checks.append(
        _ok("forecast snapshots", f"{snaps} stored")
        if snaps
        else _warn(
            "forecast snapshots",
            "none stored",
            "uv run ppp forecast --date <YYYY-MM-DD> --save, "
            "or uv run ppp import-snapshots --from <file>",
        )
    )

    # --- smoke prediction -------------------------------------------------
    pid = session.scalar(
        select(PlayerGameStat.player_id)
        .group_by(PlayerGameStat.player_id)
        .having(func.count() >= 10)
        .order_by(func.count().desc())
        .limit(1)
    )
    if pid is None:
        checks.append(_fail("smoke prediction", "no player with 10+ games", "make data"))
    else:
        try:
            res = predict(
                session,
                PredictionRequest(
                    player_id=int(pid), stat_type=StatType.POINTS, line=20.5, as_of_date=today
                ),
            )
            cal = "calibrated" if res.probabilities_calibrated is not None else "uncalibrated"
            checks.append(
                _ok(
                    "smoke prediction",
                    f"{res.player_name}: proj {res.model.projection:.1f}, "
                    f"P(more) {res.probabilities.more:.3f}, {cal}",
                )
            )
        except (InsufficientDataError, PlayerNotFoundError) as exc:
            checks.append(_fail("smoke prediction", str(exc)))
    return checks


def render_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [
        f"  {c.status:4s} {c.name:{width}s}  {c.detail}"
        + (f"  -> {c.fix}" if c.fix and c.status != "OK" else "")
        for c in checks
    ]
    fails = sum(1 for c in checks if c.status == "FAIL")
    warns = sum(1 for c in checks if c.status == "WARN")
    verdict = "READY" if fails == 0 else "NOT READY"
    lines.append(f"{verdict}: {len(checks)} checks, {fails} failed, {warns} warnings")
    return "\n".join(lines)
