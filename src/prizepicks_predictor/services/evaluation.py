"""Post-game evaluation of prediction snapshots.

Snapshots are never modified. Each gets at most one ``prediction_outcomes`` row; a row
with status ``missing_result`` is provisional and is replaced when data arrives, all
other statuses are final.

Status policy
* ``evaluated``: the player's box-score row exists with minutes > 0.
* ``did_not_play``: the game's box score is loaded but the player has no row or 0 minutes.
  This is not treated as an under; probabilistic metrics are left empty.
* ``voided``: the scheduled game was postponed or canceled (the forecast was for a game
  that did not happen on that date).
* ``not_scheduled``: the schedule for that date is loaded and the player's team has no
  game on it (a manual or replay target for an off day). Final.
* ``missing_result``: the game has not been ingested yet (or no game could be matched).
Pushes (actual == integer line) are recorded; Brier/log loss are left empty for them.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import (
    Game,
    PlayerGameStat,
    PredictionOutcome,
    PredictionSnapshot,
    ScheduledGame,
    Team,
)

EPS = 1e-6


@dataclass
class EvaluationSummary:
    through: date
    considered: int = 0
    statuses: Counter[str] = field(default_factory=Counter)
    pending_future: int = 0
    reevaluated: int = 0
    notes: list[str] = field(default_factory=list)


def _find_game(session: Session, snap: PredictionSnapshot) -> Game | None:
    if snap.scheduled_game_id is not None:
        sg = session.get(ScheduledGame, snap.scheduled_game_id)
        if sg is not None:
            game = session.scalar(
                select(Game).where(
                    Game.game_date == sg.game_date,
                    Game.home_team_id == sg.home_team_id,
                    Game.away_team_id == sg.away_team_id,
                )
            )
            if game is not None:
                return game
    if snap.team:
        team = session.scalar(select(Team).where(Team.abbreviation == snap.team))
        if team is not None:
            return session.scalar(
                select(Game).where(
                    Game.game_date == snap.target_date,
                    (Game.home_team_id == team.id) | (Game.away_team_id == team.id),
                )
            )
    # last resort: any game that day in which the player has a row
    return session.scalar(
        select(Game)
        .join(PlayerGameStat, PlayerGameStat.game_id == Game.id)
        .where(Game.game_date == snap.target_date, PlayerGameStat.player_id == snap.player_id)
    )


def _team_not_scheduled(session: Session, snap: PredictionSnapshot) -> bool:
    """True when a schedule exists for the target date and the player's team is not on it."""
    if not snap.team:
        return False
    day = session.scalars(
        select(ScheduledGame).where(ScheduledGame.game_date == snap.target_date)
    ).all()
    if not day:
        return False
    team = session.scalar(select(Team).where(Team.abbreviation == snap.team))
    if team is None:
        return False
    return all(team.id not in (g.home_team_id, g.away_team_id) for g in day)


def _logloss(p: float, y: int) -> float:
    q = min(max(p, EPS), 1 - EPS)
    return -math.log(q) if y else -math.log(1 - q)


def build_outcome(
    snap: PredictionSnapshot,
    game: Game | None,
    stat_row: PlayerGameStat | None,
    *,
    status: str,
    now: datetime,
    notes: str | None = None,
) -> PredictionOutcome:
    out = PredictionOutcome(
        snapshot_id=snap.id,
        evaluated_at=now.replace(tzinfo=None),
        game_id=game.id if game else None,
        status=status,
        notes=notes,
    )
    if status != "evaluated" or stat_row is None:
        return out
    actual = int(getattr(stat_row, snap.stat_type))
    out.actual = actual
    out.actual_minutes = stat_row.minutes
    out.abs_error = abs(snap.projection - actual)
    out.sq_error = (snap.projection - actual) ** 2
    out.covered = snap.q_lo <= actual <= snap.q_hi
    if snap.line is not None and snap.p_more is not None:
        if math.isclose(actual, snap.line, abs_tol=1e-9):
            out.outcome_push = True
            out.outcome_more = None
        else:
            out.outcome_push = False
            y = int(actual > snap.line)
            out.outcome_more = bool(y)
            cond_raw = (
                snap.p_more / (snap.p_more + (snap.p_less or 0.0))
                if (snap.p_more + (snap.p_less or 0.0)) > 0
                else snap.p_more
            )
            out.brier_raw = (cond_raw - y) ** 2
            out.logloss_raw = _logloss(cond_raw, y)
            if snap.p_more_cal is not None:
                denom = snap.p_more_cal + (snap.p_less_cal or 0.0)
                cond_cal = snap.p_more_cal / denom if denom > 0 else snap.p_more_cal
                out.brier_cal = (cond_cal - y) ** 2
                out.logloss_cal = _logloss(cond_cal, y)
    return out


def evaluate_predictions(
    session: Session, *, through: date, now: datetime | None = None
) -> EvaluationSummary:
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    summary = EvaluationSummary(through=through)
    snaps = session.scalars(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.target_date <= through)
        .order_by(PredictionSnapshot.id)
    ).all()
    for snap in snaps:
        existing = session.scalar(
            select(PredictionOutcome).where(PredictionOutcome.snapshot_id == snap.id)
        )
        if existing is not None and existing.status != "missing_result":
            continue
        summary.considered += 1

        sg = session.get(ScheduledGame, snap.scheduled_game_id) if snap.scheduled_game_id else None
        if sg is not None and sg.status in ("postponed", "canceled"):
            status, game, stat_row, note = "voided", None, None, f"scheduled game {sg.status}"
        else:
            if (
                sg is not None
                and sg.tipoff_utc is not None
                and sg.tipoff_utc > now_utc.replace(tzinfo=None)
            ):
                summary.pending_future += 1
                continue
            game = _find_game(session, snap)
            stat_row = None
            if game is None and _team_not_scheduled(session, snap):
                status, note = (
                    "not_scheduled",
                    "schedule loaded; the player's team has no game on this date",
                )
            elif game is None:
                status, note = "missing_result", "no completed game found in the database yet"
            else:
                stat_row = session.scalar(
                    select(PlayerGameStat).where(
                        PlayerGameStat.game_id == game.id,
                        PlayerGameStat.player_id == snap.player_id,
                    )
                )
                if stat_row is None or stat_row.minutes <= 0:
                    status, note = (
                        "did_not_play",
                        "box score loaded without a played row for this player",
                    )
                else:
                    status, note = "evaluated", None
        outcome = build_outcome(snap, game, stat_row, status=status, now=now_utc, notes=note)
        if existing is not None:
            session.delete(existing)
            session.flush()
            summary.reevaluated += 1
        session.add(outcome)
        summary.statuses[status] += 1
    session.commit()
    return summary
