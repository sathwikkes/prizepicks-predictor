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


def test_cli_predict_happy_path(
    db_url: str, synthetic_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--database-url", db_url, "init-db"])
    main(["--database-url", db_url, "ingest-csv", str(synthetic_csv), "--source", "fx"])
    capsys.readouterr()
    code = main(
        [
            "--database-url",
            db_url,
            "predict",
            "--player-id",
            "1",
            "--stat",
            "points",
            "--line",
            "24.5",
            "--date",
            "2024-12-01",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Alex Rivera" in out and "P(more)" in out and "P(push) = 0.000" in out
    assert "model: ewma" in out and "calibration: uncalibrated" in out
    assert "12 games with minutes" in out

    code = main(
        [
            "--database-url",
            db_url,
            "predict",
            "--player-id",
            "1",
            "--stat",
            "rebounds",
            "--line",
            "8",
            "--date",
            "2024-12-01",
            "--model",
            "minutes_adjusted",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "projected minutes" in out and "P(push) = 0.000" not in out


def test_cli_predict_error_codes(
    db_url: str, synthetic_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--database-url", db_url, "init-db"])
    main(["--database-url", db_url, "ingest-csv", str(synthetic_csv)])
    base = ["--database-url", db_url, "predict", "--stat", "points", "--date", "2024-12-01"]
    assert main([*base, "--player-id", "1", "--line", "24.3"]) == 2  # invalid line
    assert "invalid request" in capsys.readouterr().err
    assert main([*base, "--player-id", "999", "--line", "24.5"]) == 1  # unknown player
    assert (
        main(
            [
                "--database-url",
                db_url,
                "predict",
                "--player-id",
                "1",
                "--stat",
                "points",
                "--line",
                "24.5",
                "--date",
                "2024-10-27",
            ]
        )
        == 3
    )
    assert "insufficient data" in capsys.readouterr().err
    with pytest.raises(SystemExit):  # --date is required
        main(
            [
                "--database-url",
                db_url,
                "predict",
                "--player-id",
                "1",
                "--stat",
                "points",
                "--line",
                "24.5",
            ]
        )
