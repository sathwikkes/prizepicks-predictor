"""Pregame forecast generation with immutable snapshots.

Participants are *expected*, not confirmed: a player is considered for a scheduled game
when their most recent stored game (before the target date, within ``lookback_days``) was
for one of the two teams. Nothing from game day (lineups, minutes, injuries) is used
because none of it is available to the system before tip-off.

Cutoff rules (enforced, not advisory):
* the cutoff is the UTC time the forecast is generated;
* a game whose tip-off is at or before the cutoff is skipped (``already_started``);
* a game with no known tip-off is forecast only if its date is after the cutoff date;
* history is everything strictly before the target date (same-day earlier games are not used).

Lines are synthetic (trailing-10-game median, half-point) unless manual targets are given.
Probabilities at synthetic lines exist so reliability can be monitored over time; they
are not market probabilities.
"""

from __future__ import annotations

import csv
import json
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.lines import synthetic_lines
from prizepicks_predictor.db.models import (
    Game,
    Player,
    PlayerGameStat,
    PredictionSnapshot,
    ScheduledGame,
    Team,
)
from prizepicks_predictor.prediction.calibrators import CalibratorRegistry
from prizepicks_predictor.prediction.features import stat_value
from prizepicks_predictor.prediction.schemas import (
    DistributionFamily,
    PredictionRequest,
    PredictionResult,
    StatType,
)
from prizepicks_predictor.prediction.service import InsufficientDataError, PredictionError, predict
from prizepicks_predictor.services.game_logs import PlayerNotFoundError, get_player_game_logs

DATA_SOURCE = "sportsdataverse_nba_stats"
LINE_SYNTHETIC = "synthetic_trailing_median"
LINE_MANUAL = "manual"


class ForecastError(Exception):
    pass


@dataclass(frozen=True)
class ManualTarget:
    player_id: int
    stat: StatType
    line: float | None = None


@dataclass(frozen=True)
class Participant:
    player_id: int
    team: str
    scheduled_game: ScheduledGame | None
    status: str  # expected | manual


@dataclass(frozen=True)
class ForecastRow:
    player_id: int
    player_name: str
    team: str | None
    opponent: str | None
    stat: str
    line: float
    line_source: str
    projection: float
    p_more: float
    p_more_cal: float | None
    sample_size: int
    saved: bool
    duplicate: bool
    warnings: tuple[str, ...]


@dataclass
class ForecastSummary:
    batch_id: str
    target_date: date
    cutoff_at: datetime
    scheduled_games: int
    forecastable_games: int
    participants: int
    saved: int = 0
    duplicates: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    rows: list[ForecastRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def read_targets_file(path: Path) -> list[ManualTarget]:
    """CSV with columns player_id, stat[, line]."""
    targets: list[ManualTarget] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            line_raw = (row.get("line") or "").strip()
            targets.append(
                ManualTarget(
                    player_id=int(row["player_id"]),
                    stat=StatType(row["stat"].strip().lower()),
                    line=float(line_raw) if line_raw else None,
                )
            )
    return targets


def scheduled_games_on(session: Session, target_date: date) -> list[ScheduledGame]:
    return list(
        session.scalars(
            select(ScheduledGame)
            .where(ScheduledGame.game_date == target_date)
            .order_by(ScheduledGame.tipoff_utc, ScheduledGame.id)
        )
    )


def expected_participants(
    session: Session,
    games: Sequence[ScheduledGame],
    *,
    target_date: date,
    lookback_days: int,
) -> list[Participant]:
    """Players whose latest stored game before ``target_date`` was for a team playing."""
    by_team: dict[int, ScheduledGame] = {}
    for g in games:
        by_team[g.home_team_id] = g
        by_team[g.away_team_id] = g
    if not by_team:
        return []
    since = target_date - timedelta(days=lookback_days)
    latest = (
        select(PlayerGameStat.player_id, func.max(Game.game_date).label("last_date"))
        .join(Game, PlayerGameStat.game_id == Game.id)
        .where(Game.game_date < target_date, Game.game_date >= since, Game.season_type == "regular")
        .group_by(PlayerGameStat.player_id)
        .subquery()
    )
    stmt = (
        select(PlayerGameStat.player_id, PlayerGameStat.team_id, Team.abbreviation)
        .join(Game, PlayerGameStat.game_id == Game.id)
        .join(
            latest,
            (latest.c.player_id == PlayerGameStat.player_id)
            & (latest.c.last_date == Game.game_date),
        )
        .join(Team, PlayerGameStat.team_id == Team.id)
    )
    out: list[Participant] = []
    seen: set[int] = set()
    for player_id, team_id, abbr in session.execute(stmt):
        if player_id in seen or team_id not in by_team:
            continue
        seen.add(player_id)
        out.append(
            Participant(
                player_id=player_id, team=abbr, scheduled_game=by_team[team_id], status="expected"
            )
        )
    return sorted(out, key=lambda p: (p.scheduled_game.id if p.scheduled_game else 0, p.player_id))


def game_forecastable(game: ScheduledGame, cutoff: datetime) -> str | None:
    """None if the game can still be forecast at ``cutoff`` (UTC), else the skip reason."""
    if game.status in ("postponed", "canceled"):
        return f"game_{game.status}"
    if game.status in ("in_progress", "final"):
        return "already_started"
    if game.tipoff_utc is None:
        return None if game.game_date > cutoff.date() else "tipoff_unknown"
    return "already_started" if game.tipoff_utc <= cutoff.replace(tzinfo=None) else None


def _dedupe_key(
    player_id: int, target_date: date, stat: StatType, model: str, line: float, revision: int
) -> str:
    return f"{player_id}:{target_date.isoformat()}:{stat.value}:{model}:{line:g}:{revision}"


def _snapshot_from_result(
    result: PredictionResult,
    *,
    batch_id: str,
    dedupe_key: str,
    revision: int,
    participant: Participant,
    cutoff: datetime,
    line_source: str,
) -> PredictionSnapshot:
    game = participant.scheduled_game
    is_home = None
    opponent = None
    if game is not None:
        is_home = game.home_team.abbreviation == participant.team
        opponent = (game.away_team if is_home else game.home_team).abbreviation
    cal = result.probabilities_calibrated
    naive_cutoff = cutoff.replace(tzinfo=None)
    return PredictionSnapshot(
        batch_id=batch_id,
        dedupe_key=dedupe_key,
        revision=revision,
        player_id=result.player_id,
        scheduled_game_id=game.id if game else None,
        team=participant.team,
        opponent=opponent,
        is_home=is_home,
        participant_status=participant.status,
        stat_type=result.stat_type.value,
        target_date=result.as_of_date,
        cutoff_at=naive_cutoff,
        generated_at=naive_cutoff,
        tipoff_utc=game.tipoff_utc if game else None,
        model_name=result.model.name,
        model_version=result.model.version,
        distribution_family=result.distribution.family.value,
        calibration_artifact=result.calibration.artifact_id if result.calibration else None,
        calibration_status=result.calibration_status,
        projection=result.model.projection,
        dist_mean=result.distribution.mean,
        dist_std=result.distribution.std,
        q_lo=result.interval_80[0],
        q_hi=result.interval_80[1],
        line=result.line,
        line_source=line_source,
        p_more=result.probabilities.more,
        p_less=result.probabilities.less,
        p_push=result.probabilities.push,
        p_more_cal=cal.more if cal else None,
        p_less_cal=cal.less if cal else None,
        p_push_cal=cal.push if cal else None,
        sample_size=result.sample_size,
        season_games=result.features.season_games,
        history_latest_game_date=result.features.cutoff_last_game_date,
        data_source=DATA_SOURCE,
        features_json=result.features.model_dump_json(),
        warnings_json=json.dumps([w.value for w in result.warnings]),
    )


def run_forecast(
    session: Session,
    *,
    target_date: date,
    stats: Sequence[StatType],
    model_name: str = "ewma",
    distribution: DistributionFamily = DistributionFamily.AUTO,
    manual_targets: Sequence[ManualTarget] | None = None,
    save: bool = False,
    revise: bool = False,
    lookback_days: int = 21,
    now: datetime | None = None,
    calibrators: CalibratorRegistry | None = None,
    batch_id: str | None = None,
) -> ForecastSummary:
    cutoff = (now or datetime.now(UTC)).astimezone(UTC)
    batch = batch_id or f"fc_{cutoff:%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:6]}"
    games = scheduled_games_on(session, target_date)
    skip_reasons = {g.id: game_forecastable(g, cutoff) for g in games}
    open_games = [g for g in games if skip_reasons[g.id] is None]
    summary = ForecastSummary(
        batch_id=batch,
        target_date=target_date,
        cutoff_at=cutoff,
        scheduled_games=len(games),
        forecastable_games=len(open_games),
        participants=0,
    )
    for g in games:
        if skip_reasons[g.id]:
            summary.skipped[f"game:{skip_reasons[g.id]}"] += 1

    targets: list[tuple[Participant, StatType, float | None]] = []
    if manual_targets is not None:
        if target_date <= cutoff.date() and not games:
            summary.notes.append("manual targets with no schedule row: tip-off cannot be verified")
        by_team_game: dict[int, ScheduledGame] = {}
        for g in open_games:
            by_team_game[g.home_team_id] = g
            by_team_game[g.away_team_id] = g
        for t in manual_targets:
            player = session.get(Player, t.player_id)
            if player is None:
                summary.skipped["player:unknown"] += 1
                continue
            recent = get_player_game_logs(session, t.player_id, before=target_date, limit=1)
            team_abbr = recent[0].team if recent else None
            team_row = (
                session.scalar(select(Team).where(Team.abbreviation == team_abbr))
                if team_abbr
                else None
            )
            game = by_team_game.get(team_row.id) if team_row else None
            if game is None and team_row is not None:
                blocked = next(
                    (g for g in games if team_row.id in (g.home_team_id, g.away_team_id)), None
                )
                if blocked is not None and skip_reasons.get(blocked.id):
                    summary.skipped["player:game_not_forecastable"] += 1
                    continue
            targets.append(
                (Participant(t.player_id, team_abbr or "?", game, "manual"), t.stat, t.line)
            )
    else:
        if not games:
            summary.notes.append(
                "no scheduled games for this date; load a schedule or pass manual targets"
            )
        participants = expected_participants(
            session, open_games, target_date=target_date, lookback_days=lookback_days
        )
        summary.participants = len(participants)
        for p in participants:
            for stat in stats:
                targets.append((p, stat, None))

    for participant, stat, manual_line in targets:
        history = get_player_game_logs(session, participant.player_id, before=target_date, limit=10)
        played = [r for r in history if r.minutes > 0]
        if manual_line is not None:
            line, line_source = manual_line, LINE_MANUAL
        elif played:
            line = synthetic_lines(
                [float(stat_value(r, stat)) for r in played], window=10, offsets=[0]
            )[0]
            line_source = LINE_SYNTHETIC
        else:
            summary.skipped["player:no_history"] += 1
            continue
        venue: bool | None = None
        opponent_abbr: str | None = None
        if participant.scheduled_game is not None:
            g_ = participant.scheduled_game
            venue = g_.home_team.abbreviation == participant.team
            opponent_abbr = (g_.away_team if venue else g_.home_team).abbreviation
        try:
            request = PredictionRequest(
                player_id=participant.player_id,
                stat_type=stat,
                line=line,
                as_of_date=target_date,
                model_name=model_name,
                distribution=distribution,
                is_home=venue,
                opponent=opponent_abbr,
            )
            result = predict(session, request, calibrators=calibrators)
        except InsufficientDataError:
            summary.skipped["player:insufficient_history"] += 1
            continue
        except (PredictionError, PlayerNotFoundError, ValueError) as exc:
            summary.skipped[f"player:{type(exc).__name__}"] += 1
            continue
        if (
            result.features.cutoff_last_game_date
            and result.features.cutoff_last_game_date >= target_date
        ):
            raise ForecastError("history leaked past the target date")  # pragma: no cover

        revision = 1
        key = _dedupe_key(participant.player_id, target_date, stat, model_name, line, revision)
        existing = session.scalar(
            select(PredictionSnapshot).where(PredictionSnapshot.dedupe_key == key)
        )
        if existing is not None and revise:
            revision = (
                session.scalar(
                    select(func.max(PredictionSnapshot.revision)).where(
                        PredictionSnapshot.dedupe_key.like(
                            f"{participant.player_id}:{target_date.isoformat()}:{stat.value}:{model_name}:{line:g}:%"
                        )
                    )
                )
                or 1
            ) + 1
            key = _dedupe_key(participant.player_id, target_date, stat, model_name, line, revision)
            existing = None
        duplicate = existing is not None
        saved = False
        if save and not duplicate:
            session.add(
                _snapshot_from_result(
                    result,
                    batch_id=batch,
                    dedupe_key=key,
                    revision=revision,
                    participant=participant,
                    cutoff=cutoff,
                    line_source=line_source,
                )
            )
            saved = True
            summary.saved += 1
        if duplicate:
            summary.duplicates += 1
        opp = None
        if participant.scheduled_game is not None:
            g = participant.scheduled_game
            opp = (
                g.away_team if g.home_team.abbreviation == participant.team else g.home_team
            ).abbreviation
        summary.rows.append(
            ForecastRow(
                player_id=result.player_id,
                player_name=result.player_name,
                team=participant.team,
                opponent=opp,
                stat=stat.value,
                line=line,
                line_source=line_source,
                projection=result.model.projection,
                p_more=result.probabilities.more,
                p_more_cal=result.probabilities_calibrated.more
                if result.probabilities_calibrated
                else None,
                sample_size=result.sample_size,
                saved=saved,
                duplicate=duplicate,
                warnings=tuple(w.value for w in result.warnings),
            )
        )
    if save:
        session.commit()
    return summary
