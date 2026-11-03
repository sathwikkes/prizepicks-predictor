"""Team box-score lines for opponent features, keyed by team abbreviation, date-ordered."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from prizepicks_predictor.db.models import Game, Season, Team, TeamGameStat
from prizepicks_predictor.prediction.opponent_context import TeamGameLine, pace48, possessions


def load_team_lines(
    session: Session, *, before: date | None = None, season_types: Sequence[str] = ("regular",)
) -> dict[str, list[TeamGameLine]]:
    own = aliased(TeamGameStat)
    opp = aliased(TeamGameStat)
    team = aliased(Team)
    stmt = (
        select(team.abbreviation, Game.game_date, Season.start_year, own, opp)
        .join(Game, own.game_id == Game.id)
        .join(Season, Game.season_id == Season.id)
        .join(team, own.team_id == team.id)
        .join(opp, (opp.game_id == own.game_id) & (opp.team_id != own.team_id))
        .where(Game.season_type.in_(list(season_types)))
        .order_by(Game.game_date, Game.id)
    )
    if before is not None:
        stmt = stmt.where(Game.game_date < before)
    out: dict[str, list[TeamGameLine]] = {}
    for abbr, gdate, sy, o, p in session.execute(stmt):
        own_poss = possessions(o.fga, o.oreb, o.tov, o.fta)
        opp_poss = possessions(p.fga, p.oreb, p.tov, p.fta)
        out.setdefault(abbr, []).append(
            TeamGameLine(
                game_date=gdate,
                season_start_year=int(sy),
                possessions=own_poss,
                pace48=pace48((own_poss + opp_poss) / 2.0, o.minutes),
                points_allowed=float(p.points),
                opp_possessions=opp_poss,
            )
        )
    return out
