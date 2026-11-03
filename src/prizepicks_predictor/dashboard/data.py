"""Dashboard-facing read functions. No Streamlit imports, so everything here is testable.

Each function takes a SQLAlchemy session (or a path) and returns plain dataclasses /
Pydantic models that the pages render. Errors that a user should see are returned as
messages rather than raised, except programming errors.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import (
    Game,
    Player,
    PlayerGameStat,
    PredictionOutcome,
    PredictionSnapshot,
    ScheduledGame,
    Season,
    Team,
)
from prizepicks_predictor.prediction import (
    InsufficientDataError,
    PredictionError,
    PredictionRequest,
    PredictionResult,
    StatType,
    predict,
)
from prizepicks_predictor.prediction.calibrators import CalibratorRegistry
from prizepicks_predictor.prediction.schemas import DistributionFamily
from prizepicks_predictor.schemas.game_log import GameLogRow
from prizepicks_predictor.services.game_logs import (
    PlayerNotFoundError,
    find_players,
    get_player_game_logs,
)
from prizepicks_predictor.services.monitoring import SnapshotStats
from prizepicks_predictor.services.monitoring import snapshot_stats as snapshot_stats


# ----------------------------------------------------------------------------- players
@dataclass(frozen=True)
class PlayerSummary:
    player_id: int
    name: str
    position: str | None
    team: str | None
    games: int
    first_game: date | None
    last_game: date | None


def search_players(session: Session, query: str, *, limit: int = 25) -> list[PlayerSummary]:
    """Accent-insensitive name search with per-player game counts and latest team."""
    if not query.strip():
        return []
    out: list[PlayerSummary] = []
    for p in find_players(session, query, limit=limit):
        row = session.execute(
            select(func.count(), func.min(Game.game_date), func.max(Game.game_date))
            .select_from(PlayerGameStat)
            .join(Game, PlayerGameStat.game_id == Game.id)
            .where(PlayerGameStat.player_id == p.id)
        ).one()
        latest = get_player_game_logs(session, p.id, limit=1, season_types=None)
        out.append(
            PlayerSummary(
                player_id=p.id,
                name=p.full_name,
                position=p.position,
                team=latest[0].team if latest else None,
                games=int(row[0]),
                first_game=row[1],
                last_game=row[2],
            )
        )
    return out


@dataclass(frozen=True)
class SeasonLine:
    season: str
    start_year: int
    games: int
    minutes: float
    points: float
    rebounds: float
    assists: float
    teams: str


@dataclass(frozen=True)
class PlayerProfile:
    summary: PlayerSummary
    seasons: list[SeasonLine]


def player_profile(session: Session, player_id: int) -> PlayerProfile | None:
    player = session.get(Player, player_id)
    if player is None:
        return None
    base = search_players(session, player.full_name, limit=50)
    summary = next((b for b in base if b.player_id == player_id), None)
    if summary is None:
        summary = PlayerSummary(player_id, player.full_name, player.position, None, 0, None, None)
    rows = session.execute(
        select(
            Season.label,
            Season.start_year,
            func.count(),
            func.avg(PlayerGameStat.minutes),
            func.avg(PlayerGameStat.points),
            func.avg(PlayerGameStat.rebounds),
            func.avg(PlayerGameStat.assists),
            func.group_concat(Team.abbreviation.distinct()),
        )
        .select_from(PlayerGameStat)
        .join(Game, PlayerGameStat.game_id == Game.id)
        .join(Season, Game.season_id == Season.id)
        .join(Team, PlayerGameStat.team_id == Team.id)
        .where(PlayerGameStat.player_id == player_id, Game.season_type == "regular")
        .group_by(Season.label, Season.start_year)
        .order_by(Season.start_year.desc())
    ).all()
    seasons = [
        SeasonLine(
            label,
            int(sy),
            int(n),
            float(m or 0),
            float(p or 0),
            float(r or 0),
            float(a or 0),
            str(t or ""),
        )
        for label, sy, n, m, p, r, a, t in rows
    ]
    return PlayerProfile(summary=summary, seasons=seasons)


def player_logs(
    session: Session,
    player_id: int,
    *,
    season_start_year: int | None = None,
    limit: int | None = 25,
    include_playoffs: bool = False,
) -> list[GameLogRow]:
    return get_player_game_logs(
        session,
        player_id,
        season_start_year=season_start_year,
        limit=limit,
        season_types=None if include_playoffs else ("regular",),
    )


# ---------------------------------------------------------------------------- predict
@dataclass(frozen=True)
class PredictionView:
    result: PredictionResult | None
    error: str | None
    error_kind: str | None = None  # insufficient_data | not_found | invalid | engine


def run_prediction(
    session: Session,
    *,
    player_id: int,
    stat: StatType,
    line: float,
    target_date: date,
    model_name: str = "ewma",
    distribution: DistributionFamily = DistributionFamily.AUTO,
    calibrate: bool = True,
    calibrators: CalibratorRegistry | None = None,
) -> PredictionView:
    try:
        request = PredictionRequest(
            player_id=player_id,
            stat_type=stat,
            line=line,
            as_of_date=target_date,
            model_name=model_name,
            distribution=distribution,
            calibrate=calibrate,
        )
    except ValidationError as exc:
        return PredictionView(None, f"Invalid request: {exc.errors()[0]['msg']}", "invalid")
    try:
        return PredictionView(predict(session, request, calibrators=calibrators), None)
    except InsufficientDataError as exc:
        return PredictionView(None, str(exc), "insufficient_data")
    except PlayerNotFoundError as exc:
        return PredictionView(None, str(exc), "not_found")
    except PredictionError as exc:
        return PredictionView(None, str(exc), "engine")


# -------------------------------------------------------------------------- snapshots
@dataclass(frozen=True)
class SnapshotRow:
    snapshot_id: int
    batch_id: str
    revision: int
    target_date: date
    player_id: int
    player_name: str
    team: str | None
    opponent: str | None
    is_home: bool | None
    stat: str
    line: float | None
    line_source: str
    projection: float
    q_lo: float
    q_hi: float
    p_more: float | None
    p_more_cal: float | None
    sample_size: int
    model: str
    calibration_status: str
    cutoff_at: datetime
    tipoff_utc: datetime | None
    kind: str  # prospective | unverified
    participant_status: str
    status: str  # pending | evaluated | did_not_play | voided | not_scheduled | missing_result
    actual: int | None
    abs_error: float | None
    covered: bool | None
    outcome_more: bool | None
    outcome_push: bool | None
    warnings: tuple[str, ...]


def snapshot_kind(cutoff_at: datetime, tipoff_utc: datetime | None) -> str:
    """'prospective' only when the forecast was saved before a known tip-off."""
    return "prospective" if tipoff_utc is not None and cutoff_at < tipoff_utc else "unverified"


def snapshot_dates(session: Session) -> list[tuple[date, int]]:
    rows = session.execute(
        select(PredictionSnapshot.target_date, func.count())
        .group_by(PredictionSnapshot.target_date)
        .order_by(PredictionSnapshot.target_date.desc())
    ).all()
    return [(d, int(n)) for d, n in rows]


def snapshot_games(session: Session, target_date: date) -> list[tuple[int, str]]:
    games = session.scalars(
        select(ScheduledGame)
        .where(ScheduledGame.game_date == target_date)
        .order_by(ScheduledGame.tipoff_utc)
    ).all()
    return [
        (g.id, f"{g.away_team.abbreviation} @ {g.home_team.abbreviation} ({g.status})")
        for g in games
    ]


def list_snapshots(
    session: Session,
    *,
    target_date: date | None = None,
    scheduled_game_id: int | None = None,
    player_query: str | None = None,
    stat: StatType | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[SnapshotRow]:
    stmt = (
        select(PredictionSnapshot, Player, PredictionOutcome)
        .join(Player, PredictionSnapshot.player_id == Player.id)
        .outerjoin(PredictionOutcome, PredictionOutcome.snapshot_id == PredictionSnapshot.id)
        .order_by(
            PredictionSnapshot.target_date.desc(), Player.full_name, PredictionSnapshot.stat_type
        )
    )
    if target_date is not None:
        stmt = stmt.where(PredictionSnapshot.target_date == target_date)
    if scheduled_game_id is not None:
        stmt = stmt.where(PredictionSnapshot.scheduled_game_id == scheduled_game_id)
    if stat is not None:
        stmt = stmt.where(PredictionSnapshot.stat_type == stat.value)
    if player_query:
        ids = [p.id for p in find_players(session, player_query, limit=50)]
        stmt = stmt.where(PredictionSnapshot.player_id.in_(ids or [-1]))
    if status == "pending":
        stmt = stmt.where(PredictionOutcome.id.is_(None))
    elif status is not None:
        stmt = stmt.where(PredictionOutcome.status == status)
    out: list[SnapshotRow] = []
    for snap, player, outcome in session.execute(stmt.limit(limit)):
        st_ = "pending" if outcome is None else outcome.status
        out.append(
            SnapshotRow(
                snapshot_id=snap.id,
                batch_id=snap.batch_id,
                revision=snap.revision,
                target_date=snap.target_date,
                player_id=snap.player_id,
                player_name=player.full_name,
                team=snap.team,
                opponent=snap.opponent,
                is_home=snap.is_home,
                stat=snap.stat_type,
                line=snap.line,
                line_source=snap.line_source,
                projection=snap.projection,
                q_lo=snap.q_lo,
                q_hi=snap.q_hi,
                p_more=snap.p_more,
                p_more_cal=snap.p_more_cal,
                sample_size=snap.sample_size,
                model=f"{snap.model_name}-{snap.model_version}",
                calibration_status=snap.calibration_status,
                cutoff_at=snap.cutoff_at,
                tipoff_utc=snap.tipoff_utc,
                kind=snapshot_kind(snap.cutoff_at, snap.tipoff_utc),
                participant_status=snap.participant_status,
                status=st_,
                actual=outcome.actual if outcome else None,
                abs_error=outcome.abs_error if outcome else None,
                covered=outcome.covered if outcome else None,
                outcome_more=outcome.outcome_more if outcome else None,
                outcome_push=outcome.outcome_push if outcome else None,
                warnings=tuple(json.loads(snap.warnings_json)),
            )
        )
    return out


# -------------------------------------------------------------------------- backtests
@dataclass
class BacktestRun:
    name: str
    path: Path
    meta: dict[str, Any]
    comparison: list[dict[str, str]]
    reliability: list[dict[str, str]]
    strata: list[dict[str, str]]
    calibration: list[dict[str, str]] = field(default_factory=list)
    summary_md: str = ""

    @property
    def seasons(self) -> str:
        return ", ".join(
            f"{s}-{(int(s) + 1) % 100:02d}" for s in self.meta.get("config", {}).get("seasons", [])
        )

    @property
    def role(self) -> str:
        n = self.name.lower()
        return (
            "held-out test"
            if n.startswith("test")
            else "validation"
            if n.startswith("val")
            else "development"
            if n.startswith("dev")
            else "other"
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_backtest_runs(base: Path) -> list[BacktestRun]:
    runs: list[BacktestRun] = []
    if not base.is_dir():
        return runs
    for run_dir in sorted(p for p in base.iterdir() if p.is_dir() and (p / "run.json").is_file()):
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        calibration: list[dict[str, str]] = []
        for cal in sorted(run_dir.glob("calibration_*.csv")):
            parts = cal.stem.split("_")  # calibration_<stat>_<model>_<family>
            for row in _read_csv(cal):
                calibration.append(
                    {**row, "stat": parts[1], "model": "_".join(parts[2:-1]), "family": parts[-1]}
                )
        runs.append(
            BacktestRun(
                name=run_dir.name,
                path=run_dir,
                meta=meta,
                comparison=_read_csv(run_dir / "comparison.csv"),
                reliability=_read_csv(run_dir / "reliability.csv"),
                strata=_read_csv(run_dir / "strata.csv"),
                calibration=calibration,
                summary_md=(run_dir / "summary.md").read_text(encoding="utf-8")
                if (run_dir / "summary.md").is_file()
                else "",
            )
        )
    order = {"development": 0, "validation": 1, "held-out test": 2, "other": 3}
    return sorted(runs, key=lambda r: (order[r.role], r.name))


# ------------------------------------------------------------------------------ status
@dataclass(frozen=True)
class CoverageRow:
    season: str
    season_type: str
    games: int
    player_games: int
    players: int
    first_game: date
    last_game: date


@dataclass
class DataStatus:
    now_utc: datetime
    latest_game_date: date | None
    box_score_lag_days: int | None
    coverage: list[CoverageRow]
    scheduled_by_status: dict[str, int]
    schedule_loaded_at: datetime | None
    schedule_last_final: date | None
    next_game_date: date | None
    next_game_count: int
    snapshots: SnapshotStats
    artifacts: list[dict[str, str]]
    warnings: list[str] = field(default_factory=list)


def data_status(
    session: Session, *, calibration_dir: Path, now: datetime | None = None
) -> DataStatus:
    now_utc = (now or datetime.now(UTC)).replace(tzinfo=None)
    latest = session.scalar(select(func.max(Game.game_date)))
    cov_rows = session.execute(
        select(
            Season.label,
            Game.season_type,
            func.count(func.distinct(Game.id)),
            func.count(PlayerGameStat.id),
            func.count(func.distinct(PlayerGameStat.player_id)),
            func.min(Game.game_date),
            func.max(Game.game_date),
        )
        .join(Game, Game.season_id == Season.id)
        .outerjoin(PlayerGameStat, PlayerGameStat.game_id == Game.id)
        .group_by(Season.label, Game.season_type)
        .order_by(Season.label, Game.season_type)
    ).all()
    coverage = [CoverageRow(a, b, int(c), int(d), int(e), f, g) for a, b, c, d, e, f, g in cov_rows]
    by_status = {
        s: int(n)
        for s, n in session.execute(
            select(ScheduledGame.status, func.count()).group_by(ScheduledGame.status)
        )
    }
    loaded_at = session.scalar(select(func.max(ScheduledGame.loaded_at)))
    last_final = session.scalar(
        select(func.max(ScheduledGame.game_date)).where(ScheduledGame.status == "final")
    )
    next_date = session.scalar(
        select(func.min(ScheduledGame.game_date)).where(
            ScheduledGame.status == "scheduled", ScheduledGame.game_date >= now_utc.date()
        )
    )
    next_count = (
        int(session.scalar(select(func.count()).where(ScheduledGame.game_date == next_date)) or 0)
        if next_date
        else 0
    )
    artifacts: list[dict[str, str]] = []
    for art in CalibratorRegistry.load(calibration_dir).artifacts:
        held = art.held_out
        artifacts.append(
            {
                "stat": art.stat.value,
                "model": f"{art.model_name}-{art.model_version}",
                "family": art.family.value,
                "method": art.method,
                "fit_seasons": ", ".join(f"{s}-{(s + 1) % 100:02d}" for s in art.fit_seasons),
                "validation": art.validation_status,
                "held_out_ece": f"{held.ece_uncalibrated:.4f} → {held.ece_calibrated:.4f}"
                if held
                else "n/a",
                "artifact_id": art.artifact_id,
            }
        )
    warnings: list[str] = []
    lag = None
    if latest is not None and last_final is not None and last_final > latest:
        lag = (last_final - latest).days
        warnings.append(
            f"Box scores end {latest} but the schedule shows final games through {last_final}: "
            f"{lag} day(s) behind. Run `ppp ingest-sdv --season <END_YEAR> --force-download`."
        )
    if loaded_at is not None and (now_utc - loaded_at).days >= 2:
        warnings.append(
            f"Schedule last loaded {loaded_at:%Y-%m-%d %H:%M} UTC; "
            "reload with `ppp ingest-schedule`."
        )
    if not by_status:
        warnings.append(
            "No schedule loaded: forecasts need `ppp ingest-schedule --season <END_YEAR>`."
        )
    if not artifacts:
        warnings.append(
            f"No calibration artifacts in {calibration_dir}; predictions will be uncalibrated."
        )
    if latest is None:
        warnings.append("No game logs loaded. Run `ppp ingest-sdv`.")
    return DataStatus(
        now_utc=now_utc,
        latest_game_date=latest,
        box_score_lag_days=lag,
        coverage=coverage,
        scheduled_by_status=by_status,
        schedule_loaded_at=loaded_at,
        schedule_last_final=last_final,
        next_game_date=next_date,
        next_game_count=next_count,
        snapshots=snapshot_stats(session),
        artifacts=artifacts,
        warnings=warnings,
    )
