from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.services import PlayerNotFoundError, find_players, get_player_game_logs
from tests.conftest import BASE_HEADER, write_csv


@pytest.fixture
def loaded(session: Session, synthetic_csv: Path) -> Session:
    load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    return session


def test_find_players_is_case_insensitive_substring(loaded: Session) -> None:
    names = [p.full_name for p in find_players(loaded, "RIV")]
    assert names == ["Alex Rivera"]


def test_game_logs_by_name_sorted_desc(loaded: Session) -> None:
    rows = get_player_game_logs(loaded, "Alex Rivera")
    assert len(rows) == 12
    assert rows[0].player_name == "Alex Rivera"
    assert rows[0].team == "BOS"
    assert rows[0].season == "2024-25"
    dates = [r.game_date for r in rows]
    assert dates == sorted(dates, reverse=True)
    for r in rows:
        assert r.opponent != r.team
        assert r.minutes >= 0 and r.points >= 0


def test_game_logs_by_id_and_limit(loaded: Session) -> None:
    player = find_players(loaded, "Okafor")[0]
    rows = get_player_game_logs(loaded, player.id, limit=3)
    assert len(rows) == 3
    assert all(r.player_id == player.id for r in rows)


def test_before_filter_excludes_target_date(loaded: Session) -> None:
    rows = get_player_game_logs(loaded, "Alex Rivera")
    cutoff = rows[5].game_date
    before = get_player_game_logs(loaded, "Alex Rivera", before=cutoff)
    assert all(r.game_date < cutoff for r in before)
    assert len(before) == len([r for r in rows if r.game_date < cutoff])


def test_season_filter(loaded: Session) -> None:
    assert len(get_player_game_logs(loaded, "Alex Rivera", season_start_year=2024)) == 12
    assert get_player_game_logs(loaded, "Alex Rivera", season_start_year=2019) == []


def test_unknown_player_raises(loaded: Session) -> None:
    with pytest.raises(PlayerNotFoundError):
        get_player_game_logs(loaded, "Nobody Here")
    with pytest.raises(PlayerNotFoundError):
        get_player_game_logs(loaded, 999_999)


def test_ambiguous_name_raises(session: Session, tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "amb.csv",
        BASE_HEADER,
        "2024-11-01,P1,Chris Example,BOS,LAL,home,30:00,20,5,4",
        "2024-11-01,P2,Chris Exampleton,LAL,BOS,away,30:00,20,5,4",
    )
    load_game_logs(session, CsvGameLogProvider(path))
    with pytest.raises(PlayerNotFoundError, match="ambiguous"):
        get_player_game_logs(session, "Chris Ex")
    # Exact match wins even when a substring match also exists.
    assert len(get_player_game_logs(session, "Chris Example")) == 1


def test_home_away_resolution(session: Session, tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "ha.csv",
        BASE_HEADER,
        "2024-11-01,P1,Pat Example,BOS,LAL,away,30:00,20,5,4",
    )
    load_game_logs(session, CsvGameLogProvider(path))
    row = get_player_game_logs(session, "Pat Example")[0]
    assert row.game_date == date(2024, 11, 1)
    assert row.is_home is False and row.team == "BOS" and row.opponent == "LAL"
