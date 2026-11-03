from __future__ import annotations

import json
from pathlib import Path

import pytest

from prizepicks_predictor.cli import main


def test_cli_backtest_smoke_and_calibrate(
    db_url: str, synthetic_csv: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--database-url", db_url, "init-db"])
    main(["--database-url", db_url, "ingest-csv", str(synthetic_csv), "--source", "fx"])
    capsys.readouterr()
    out_dir = tmp_path / "bt"
    code = main(
        [
            "--database-url",
            db_url,
            "backtest",
            "--season",
            "2024-25",
            "--stat",
            "points",
            "--min-prior-games",
            "3",
            "--max-observations",
            "20",
            "--output-dir",
            str(out_dir),
            "--run-name",
            "smoke",
            "--no-charts",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "observations=20" in out and "season_average" in out and "summary:" in out
    meta = json.loads((out_dir / "smoke" / "run.json").read_text())
    assert meta["config"]["seasons"] == [2024] and meta["observations"] == 20
    assert (out_dir / "smoke" / "predictions.csv.gz").is_file()

    # a second run to calibrate against; same data but different sample
    code = main(
        [
            "--database-url",
            db_url,
            "backtest",
            "--season",
            "2024-25",
            "--stat",
            "points",
            "--min-prior-games",
            "3",
            "--max-observations",
            "20",
            "--seed",
            "1",
            "--output-dir",
            str(out_dir),
            "--run-name",
            "smoke2",
            "--no-charts",
        ]
    )
    assert code == 0
    capsys.readouterr()
    code = main(
        [
            "--database-url",
            db_url,
            "calibrate",
            "--fit-run",
            str(out_dir / "smoke"),
            "--eval-run",
            str(out_dir / "smoke2"),
            "--stat",
            "points",
            "--no-charts",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "unsupported" in out  # far fewer than MIN_FIT_SIZE pairs
    assert (out_dir / "smoke2" / "calibration_points_ewma_auto.csv").is_file()


def test_cli_backtest_rejects_bad_config(db_url: str, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--database-url", db_url, "init-db"])
    assert main(["--database-url", db_url, "backtest", "--season", "2024-26"]) == 2
    assert "invalid backtest configuration" in capsys.readouterr().err
    assert (
        main(["--database-url", db_url, "backtest", "--season", "2024-25", "--models", "xgboost"])
        == 2
    )


def test_cli_calibrate_same_run_rejected(
    db_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--database-url", db_url, "init-db"])
    (tmp_path / "r").mkdir()
    assert (
        main(
            [
                "--database-url",
                db_url,
                "calibrate",
                "--fit-run",
                str(tmp_path / "r"),
                "--eval-run",
                str(tmp_path / "r"),
                "--stat",
                "points",
            ]
        )
        == 2
    )
