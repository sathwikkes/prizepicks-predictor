"""Bulk history loading: every stored game log, grouped by player, in date order.

One query instead of one per prediction. ~80k rows load in well under a second.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from prizepicks_predictor.db.models import Game, Player, PlayerGameStat, Season, Team
from prizepicks_predictor.schemas.game_log import GameLogRow


def load_history(
    session: Session,
    *,
    season_types: Sequence[str] = ("regular",),
    before: date | None = None,
) -> tuple[dict[int, list[GameLogRow]], dict[int, str]]:
    """Return ``{player_id: [GameLogRow ascending by date]}`` and ``{player_id: name}``."""
    own = aliased(Team)
    home = aliased(Team)
    away = aliased(Team)
    stmt = (
        select(PlayerGameStat, Game, Season, Player, own, home, away)
        .join(Game, PlayerGameStat.game_id == Game.id)
        .join(Season, Game.season_id == Season.id)
        .join(Player, PlayerGameStat.player_id == Player.id)
        .join(own, PlayerGameStat.team_id == own.id)
        .join(home, Game.home_team_id == home.id)
        .join(away, Game.away_team_id == away.id)
        .where(Game.season_type.in_(list(season_types)))
        .order_by(PlayerGameStat.player_id, Game.game_date, Game.id)
    )
    if before is not None:
        stmt = stmt.where(Game.game_date < before)

    history: dict[int, list[GameLogRow]] = {}
    names: dict[int, str] = {}
    for stat, game, season, player, team, home_t, away_t in session.execute(stmt):
        is_home = game.home_team_id == team.id
        names[player.id] = player.full_name
        history.setdefault(player.id, []).append(
            GameLogRow(
                game_date=game.game_date,
                season=season.label,
                season_type=game.season_type,
                player_id=player.id,
                player_name=player.full_name,
                team=team.abbreviation,
                opponent=(away_t if is_home else home_t).abbreviation,
                is_home=is_home,
                minutes=stat.minutes,
                points=stat.points,
                rebounds=stat.rebounds,
                assists=stat.assists,
                steals=stat.steals,
                blocks=stat.blocks,
                turnovers=stat.turnovers,
                three_pointers_made=stat.three_pointers_made,
                started=stat.started,
            )
        )
    return history, names
