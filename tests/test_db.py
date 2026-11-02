from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from prizepicks_predictor.db import (
    Game,
    Player,
    PlayerGameStat,
    Season,
    Team,
    create_db_engine,
    init_db,
)

EXPECTED_TABLES = {
    "seasons",
    "teams",
    "players",
    "games",
    "player_game_stats",
    "ingestion_runs",
    "external_ids",
}


def test_init_db_creates_all_tables(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_init_db_is_idempotent(engine: Engine) -> None:
    init_db(engine)
    init_db(engine)
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_sqlite_parent_directory_is_created(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "app.db"
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    assert db_path.is_file()


def test_season_label() -> None:
    assert Season.label_for(2024) == "2024-25"
    assert Season.label_for(1999) == "1999-00"


def _seed_game(session: Session) -> tuple[Game, Player, Team]:
    season = Season(start_year=2024, label="2024-25")
    bos, lal = Team(abbreviation="BOS"), Team(abbreviation="LAL")
    player = Player(full_name="Test Player")
    session.add_all([season, bos, lal, player])
    session.flush()
    game = Game(
        season_id=season.id,
        game_date=date(2024, 11, 1),
        home_team_id=bos.id,
        away_team_id=lal.id,
    )
    session.add(game)
    session.flush()
    return game, player, bos


def test_unique_player_game_constraint(session: Session) -> None:
    game, player, team = _seed_game(session)
    kwargs = dict(
        game_id=game.id,
        player_id=player.id,
        team_id=team.id,
        minutes=30.0,
        points=10,
        rebounds=5,
        assists=3,
        updated_at=datetime(2024, 11, 2),
    )
    session.add(PlayerGameStat(**kwargs))
    session.flush()
    session.add(PlayerGameStat(**kwargs))
    with pytest.raises(IntegrityError):
        session.flush()


def test_unique_game_constraint(session: Session) -> None:
    game, _, _ = _seed_game(session)
    session.add(
        Game(
            season_id=game.season_id,
            game_date=game.game_date,
            home_team_id=game.home_team_id,
            away_team_id=game.away_team_id,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_foreign_keys_enforced(session: Session) -> None:
    session.add(Game(season_id=999, game_date=date(2024, 1, 1), home_team_id=1, away_team_id=2))
    with pytest.raises(IntegrityError):
        session.flush()
