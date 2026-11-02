from __future__ import annotations

from pathlib import Path

import pytest

from prizepicks_predictor.cli import main


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'cli.db'}"


def test_cli_end_to_end(
    db_url: str, synthetic_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--database-url", db_url, "init-db"]) == 0
    assert main(["--database-url", db_url, "ingest-csv", str(synthetic_csv), "--source", "fx"]) == 0
    out = capsys.readouterr().out
    assert "inserted=72" in out

    assert main(["--database-url", db_url, "players", "--search", "okafor"]) == 0
    assert "Sam Okafor" in capsys.readouterr().out

    assert main(["--database-url", db_url, "game-logs", "Sam Okafor", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "Sam Okafor" in out and "5 games" in out and "pts" in out

    assert main(["--database-url", db_url, "runs"]) == 0
    assert "succeeded" in capsys.readouterr().out


def test_cli_unknown_player_returns_1(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--database-url", db_url, "init-db"])
    assert main(["--database-url", db_url, "game-logs", "Nobody"]) == 1
    assert "error" in capsys.readouterr().err


def test_cli_missing_csv_returns_2(
    db_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--database-url", db_url, "init-db"])
    assert main(["--database-url", db_url, "ingest-csv", str(tmp_path / "missing.csv")]) == 2
    assert "not found" in capsys.readouterr().err
