"""Team game dates known before a target date: completed games plus loaded schedule rows."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import Game, ScheduledGame, Team


def team_game_dates_before(
    session: Session, team_abbr: str, target: date, *, season_types: tuple[str, ...] = ("regular",)
) -> tuple[list[date], str]:
    """Sorted distinct dates of the team's games strictly before ``target``.

    Completed games come from ``games``; when the box scores lag, the schedule (final,
    in-progress or scheduled rows dated before the target) fills the gap, so the most
    recent game is not missed. Returns (dates, source) where source is games | schedule |
    none.
    """
    team = session.scalar(select(Team).where(Team.abbreviation == team_abbr))
    if team is None:
        return [], "none"
    played = set(
        session.scalars(
            select(Game.game_date).where(
                (Game.home_team_id == team.id) | (Game.away_team_id == team.id),
                Game.game_date < target,
                Game.season_type.in_(list(season_types)),
            )
        )
    )
    scheduled = set(
        session.scalars(
            select(ScheduledGame.game_date).where(
                (ScheduledGame.home_team_id == team.id) | (ScheduledGame.away_team_id == team.id),
                ScheduledGame.game_date < target,
                ScheduledGame.season_type.in_(list(season_types)),
                ScheduledGame.status.in_(["final", "in_progress", "scheduled"]),
            )
        )
    )
    dates = sorted(played | scheduled)
    if not dates:
        return [], "none"
    source = "games" if not (scheduled - played) else "schedule"
    return dates, source
