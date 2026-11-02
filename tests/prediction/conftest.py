from __future__ import annotations

from datetime import date, timedelta

import pytest

from prizepicks_predictor.schemas.game_log import GameLogRow


def row(
    game_date: date,
    *,
    points: int = 20,
    rebounds: int = 5,
    assists: int = 4,
    minutes: float = 32.0,
    team: str = "BOS",
    opponent: str = "LAL",
    is_home: bool = True,
    player_id: int = 1,
) -> GameLogRow:
    return GameLogRow(
        game_date=game_date,
        season=f"{game_date.year}-{(game_date.year + 1) % 100:02d}",
        player_id=player_id,
        player_name="Test Player",
        team=team,
        opponent=opponent,
        is_home=is_home,
        minutes=minutes,
        points=points,
        rebounds=rebounds,
        assists=assists,
    )


def series(
    start: date, values: list[int], *, step_days: int = 2, **kwargs: object
) -> list[GameLogRow]:
    """Rows in DESCENDING date order (newest first) where values[0] is the OLDEST game."""
    rows = [
        row(start + timedelta(days=i * step_days), points=v, **kwargs) for i, v in enumerate(values)
    ]  # type: ignore[arg-type]
    return list(reversed(rows))


@pytest.fixture
def as_of() -> date:
    return date(2024, 12, 1)
