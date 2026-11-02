from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from prizepicks_predictor.ingestion.csv_provider import (
    parse_home_away,
    parse_minutes,
    parse_optional_bool,
    row_to_game_log,
)
from prizepicks_predictor.schemas.game_log import NormalizedGameLog, season_start_year_for

VALID = {
    "game_date": "2024-11-05",
    "player_id": "P1",
    "player_name": "Some Player",
    "team": "bos",
    "opponent": "LAL",
    "home_away": "home",
    "minutes": "34:30",
    "points": "25",
    "rebounds": "7",
    "assists": "5",
}


def test_row_to_game_log_happy_path() -> None:
    rec = row_to_game_log(VALID, source="csv")
    assert rec.team == "BOS"  # upper-cased
    assert rec.minutes == pytest.approx(34.5)
    assert rec.is_home is True
    assert rec.season_start_year == 2024  # inferred
    assert rec.home_team == "BOS" and rec.away_team == "LAL"


@pytest.mark.parametrize(
    ("month", "expected"),
    [(10, 2024), (12, 2024), (1, 2023), (6, 2023), (9, 2023)],
)
def test_season_inference(month: int, expected: int) -> None:
    assert season_start_year_for(date(2024, month, 15)) == expected


def test_explicit_season_overrides_inference() -> None:
    rec = row_to_game_log({**VALID, "season": "2023"}, source="csv")
    assert rec.season_start_year == 2023


@pytest.mark.parametrize("raw,expected", [("34:30", 34.5), ("12", 12.0), ("0:45", 0.75)])
def test_parse_minutes(raw: str, expected: float) -> None:
    assert parse_minutes(raw) == pytest.approx(expected)


def test_parse_minutes_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_minutes("  ")


@pytest.mark.parametrize(
    "raw,expected", [("home", True), ("H", True), ("away", False), ("@", False)]
)
def test_parse_home_away(raw: str, expected: bool) -> None:
    assert parse_home_away(raw) is expected


def test_parse_home_away_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_home_away("neutral")


def test_parse_optional_bool() -> None:
    assert parse_optional_bool("") is None
    assert parse_optional_bool("yes") is True
    assert parse_optional_bool("0") is False
    with pytest.raises(ValueError):
        parse_optional_bool("maybe")


@pytest.mark.parametrize(
    "overrides",
    [
        {"points": "-1"},
        {"minutes": "99"},
        {"opponent": "BOS"},  # same as team
        {"rebounds": "abc"},
        {"game_date": "11/05/2024"},
        {"player_name": "  "},
        {"home_away": "sideline"},
    ],
)
def test_invalid_rows_rejected(overrides: dict[str, str]) -> None:
    with pytest.raises((ValueError, ValidationError)):
        row_to_game_log({**VALID, **overrides}, source="csv")


def test_missing_required_key_raises_key_error() -> None:
    row = dict(VALID)
    del row["points"]
    with pytest.raises(KeyError):
        row_to_game_log(row, source="csv")


def test_normalized_game_log_is_frozen() -> None:
    rec = row_to_game_log(VALID, source="csv")
    with pytest.raises(ValidationError):
        rec.points = 99  # type: ignore[misc]


def test_dedupe_key_is_order_independent_of_home_away() -> None:
    a = NormalizedGameLog(
        source="x",
        source_player_id="P1",
        player_name="A",
        team="BOS",
        opponent="LAL",
        is_home=True,
        game_date=date(2024, 11, 1),
        minutes=30,
        points=1,
        rebounds=1,
        assists=1,
    )
    b = a.model_copy(update={"team": "LAL", "opponent": "BOS", "is_home": False})
    assert a.dedupe_key == b.dedupe_key
