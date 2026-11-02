from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prizepicks_predictor.db import ExternalId, Game, IngestionRun, Player, PlayerGameStat, Team
from prizepicks_predictor.ingestion import CsvGameLogProvider, ProviderError, load_game_logs
from tests.conftest import BASE_HEADER, write_csv


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_load_synthetic_fixture(session: Session, synthetic_csv: Path) -> None:
    result = load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))

    assert result.received == 72
    assert result.inserted == 72
    assert result.rejected == 0
    assert _count(session, PlayerGameStat) == 72
    assert _count(session, Player) == 6
    assert _count(session, Game) == 72  # each synthetic row is its own game
    assert _count(session, ExternalId) == 6 + 72  # players + games

    run = session.get(IngestionRun, result.run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.records_inserted == 72
    assert run.finished_at is not None
    assert run.source_detail is not None and "synthetic_game_logs.csv" in run.source_detail


def test_reimport_is_idempotent(session: Session, synthetic_csv: Path) -> None:
    provider = CsvGameLogProvider(synthetic_csv, source_name="csv_fixture")
    load_game_logs(session, provider)
    second = load_game_logs(session, provider)

    assert second.inserted == 0
    assert second.updated == 0
    assert second.unchanged == 72
    assert _count(session, PlayerGameStat) == 72
    assert _count(session, Player) == 6
    assert _count(session, IngestionRun) == 2


def test_changed_stat_updates_existing_row(session: Session, tmp_path: Path) -> None:
    row = "2024-11-01,P1,Pat Example,BOS,LAL,home,30:00,20,5,4"
    first = write_csv(tmp_path / "a.csv", BASE_HEADER, row)
    load_game_logs(session, CsvGameLogProvider(first))

    corrected = write_csv(tmp_path / "b.csv", BASE_HEADER, row.replace(",20,5,4", ",22,5,4"))
    result = load_game_logs(session, CsvGameLogProvider(corrected))

    assert result.updated == 1 and result.inserted == 0
    stat = session.scalar(select(PlayerGameStat))
    assert stat is not None and stat.points == 22
    assert stat.ingestion_run_id == result.run_id


def test_duplicate_rows_within_batch_collapse(session: Session, tmp_path: Path) -> None:
    row = "2024-11-01,P1,Pat Example,BOS,LAL,home,30:00,20,5,4"
    path = write_csv(tmp_path / "dup.csv", BASE_HEADER, row, row, row)
    result = load_game_logs(session, CsvGameLogProvider(path))

    assert result.received == 3
    assert result.inserted == 1
    assert result.duplicates_in_batch == 2
    assert _count(session, PlayerGameStat) == 1


def test_both_teams_share_one_game_row(session: Session, tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "game.csv",
        BASE_HEADER + ",team_score,opponent_score",
        "2024-11-01,P1,Pat Example,BOS,LAL,home,30:00,20,5,4,110,100",
        "2024-11-01,P2,Lee Example,LAL,BOS,away,28:00,15,8,2,100,110",
    )
    result = load_game_logs(session, CsvGameLogProvider(path))

    assert result.inserted == 2
    assert _count(session, Game) == 1
    game = session.scalar(select(Game))
    assert game is not None
    assert (game.home_score, game.away_score) == (110, 100)
    assert _count(session, Team) == 2


def test_bad_rows_are_rejected_and_counted(session: Session, tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "mixed.csv",
        BASE_HEADER,
        "2024-11-01,P1,Pat Example,BOS,LAL,home,30:00,20,5,4",
        "2024-11-03,P1,Pat Example,BOS,BOS,home,30:00,20,5,4",  # team == opponent
        "2024-11-05,P1,Pat Example,BOS,MIA,home,30:00,-3,5,4",  # negative points
        "not-a-date,P1,Pat Example,BOS,MIA,home,30:00,20,5,4",
    )
    result = load_game_logs(session, CsvGameLogProvider(path))

    assert result.received == 4
    assert result.inserted == 1
    assert result.rejected == 3
    assert len(result.rejections) == 3
    assert all("mixed.csv:" in r for r in result.rejections)
    run = session.get(IngestionRun, result.run_id)
    assert run is not None and run.records_rejected == 3


def test_missing_file_marks_run_failed(session: Session, tmp_path: Path) -> None:
    with pytest.raises(ProviderError):
        load_game_logs(session, CsvGameLogProvider(tmp_path / "nope.csv"))
    run = session.scalar(select(IngestionRun))
    assert run is not None
    assert run.status == "failed"
    assert run.error_message and "not found" in run.error_message


def test_missing_columns_is_provider_error(session: Session, tmp_path: Path) -> None:
    path = write_csv(tmp_path / "cols.csv", "game_date,player_id", "2024-11-01,P1")
    with pytest.raises(ProviderError, match="missing required columns"):
        load_game_logs(session, CsvGameLogProvider(path))
    assert _count(session, PlayerGameStat) == 0


def test_same_external_id_different_sources_are_distinct_players(
    session: Session, tmp_path: Path
) -> None:
    row = "2024-11-01,P1,Pat Example,BOS,LAL,home,30:00,20,5,4"
    path = write_csv(tmp_path / "x.csv", BASE_HEADER, row)
    load_game_logs(session, CsvGameLogProvider(path, source_name="src_a"))
    load_game_logs(session, CsvGameLogProvider(path, source_name="src_b"))
    # Provider IDs are namespaced by source; cross-source identity resolution is
    # deliberately out of scope for Phase 1.
    assert _count(session, Player) == 2
    assert _count(session, Game) == 1
