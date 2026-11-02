"""Querying historical player game logs."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from prizepicks_predictor.db.models import Game, Player, PlayerGameStat, Season, Team
from prizepicks_predictor.schemas.game_log import GameLogRow


class PlayerNotFoundError(LookupError):
    pass


def strip_accents(text: str) -> str:
    """'Jokić' -> 'Jokic' so searches work without typing diacritics."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def find_players(session: Session, name_query: str, *, limit: int = 20) -> list[Player]:
    """Case- and accent-insensitive substring search on player name, ordered by name.

    The database does an ``ILIKE`` first; if that finds nothing (typically because the
    stored name has diacritics the user did not type), names are compared in Python
    after stripping accents from both sides. The player table is small enough for that.
    """
    query = name_query.strip()
    pattern = f"%{query}%"
    stmt = (
        select(Player)
        .where(Player.full_name.ilike(pattern))
        .order_by(Player.full_name)
        .limit(limit)
    )
    found = list(session.scalars(stmt))
    if found:
        return found
    needle = strip_accents(query).lower()
    everyone = session.scalars(select(Player).order_by(Player.full_name))
    return [p for p in everyone if needle in strip_accents(p.full_name).lower()][:limit]


def resolve_player(session: Session, player: int | str) -> Player:
    """Resolve by internal id or by name. Name must match exactly one player."""
    if isinstance(player, int):
        found = session.get(Player, player)
        if found is None:
            raise PlayerNotFoundError(f"no player with id {player}")
        return found

    matches = find_players(session, player, limit=5)
    wanted = strip_accents(player.strip()).lower()
    exact = [p for p in matches if strip_accents(p.full_name).lower() == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise PlayerNotFoundError(f"no player matching {player!r}")
    names = ", ".join(p.full_name for p in matches)
    raise PlayerNotFoundError(f"ambiguous player name {player!r}; candidates: {names}")


def get_player_game_logs(
    session: Session,
    player: int | str,
    *,
    season_start_year: int | None = None,
    before: date | None = None,
    limit: int | None = None,
    season_types: Sequence[str] | None = ("regular",),
) -> list[GameLogRow]:
    """Return a player's game logs, most recent first.

    ``before`` excludes games on or after that date. This is the hook the
    prediction engine uses to guarantee point-in-time features (only games
    strictly before the target game are visible).

    ``season_types`` defaults to regular season only, which is what full-game
    player props are priced on. Pass ``None`` for every game type.
    """
    resolved = resolve_player(session, player)

    own_team = aliased(Team)
    home_team = aliased(Team)
    away_team = aliased(Team)

    stmt = (
        select(PlayerGameStat, Game, Season, own_team, home_team, away_team)
        .join(Game, PlayerGameStat.game_id == Game.id)
        .join(Season, Game.season_id == Season.id)
        .join(own_team, PlayerGameStat.team_id == own_team.id)
        .join(home_team, Game.home_team_id == home_team.id)
        .join(away_team, Game.away_team_id == away_team.id)
        .where(PlayerGameStat.player_id == resolved.id)
        .order_by(Game.game_date.desc(), Game.id.desc())
    )
    if season_start_year is not None:
        stmt = stmt.where(Season.start_year == season_start_year)
    if before is not None:
        stmt = stmt.where(Game.game_date < before)
    if season_types is not None:
        stmt = stmt.where(Game.season_type.in_(list(season_types)))
    if limit is not None:
        stmt = stmt.limit(limit)

    rows: list[GameLogRow] = []
    for stat, game, season, team, home, away in session.execute(stmt):
        is_home = game.home_team_id == team.id
        rows.append(
            GameLogRow(
                game_date=game.game_date,
                season=season.label,
                season_type=game.season_type,
                player_id=resolved.id,
                player_name=resolved.full_name,
                team=team.abbreviation,
                opponent=(away if is_home else home).abbreviation,
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
    return rows
