from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.cli import main
from prizepicks_predictor.db import (
    PredictionOutcome,
    PredictionSnapshot,
    create_db_engine,
    get_session_factory,
    init_db,
)
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction.schemas import StatType
from prizepicks_predictor.services.evaluation import evaluate_predictions
from prizepicks_predictor.services.forecast import ManualTarget, run_forecast
from prizepicks_predictor.services.portability import (
    PortabilityError,
    backup_database,
    export_snapshots,
    import_snapshots,
    restore_database,
    snapshot_counts,
)
from prizepicks_predictor.services.readiness import render_checks, run_checks

TARGET = date(2024, 12, 1)


@pytest.fixture
def populated(tmp_path: Path, synthetic_csv: Path) -> tuple[str, Session]:
    url = f"sqlite:///{tmp_path / 'src.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    s = get_session_factory(engine)()
    load_game_logs(s, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    run_forecast(
        s,
        target_date=TARGET,
        stats=[StatType.POINTS, StatType.REBOUNDS],
        save=True,
        now=datetime(2024, 11, 30, 12, tzinfo=UTC),
        manual_targets=[
            ManualTarget(1, StatType.POINTS, 24.5),
            ManualTarget(1, StatType.REBOUNDS, 8.0),
            ManualTarget(3, StatType.POINTS, 20.5),
        ],
    )
    # a result for player 1 so one outcome exists
    load_game_logs(
        s,
        CsvGameLogProvider(
            _csv(tmp_path, "2024-12-01,FIX-001,Alex Rivera,BOS,LAL,home,36:00,31,8,5"),
            source_name="csv_fixture",
        ),
    )
    evaluate_predictions(s, through=TARGET, now=datetime(2024, 12, 3, tzinfo=UTC))
    return url, s


def _csv(tmp_path: Path, *rows: str) -> Path:
    from tests.conftest import BASE_HEADER, write_csv

    return write_csv(tmp_path / f"r{len(rows)}.csv", BASE_HEADER, *rows)


# --- readiness ---------------------------------------------------------------------
def test_readiness_on_empty_database(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'e.db'}")
    init_db(engine)
    with get_session_factory(engine)() as s:
        checks = run_checks(s, calibration_dir=tmp_path / "none", backtest_dir=tmp_path / "nobt")
    by = {c.name: c for c in checks}
    assert by["schema"].status == "OK" and by["immutability triggers"].status == "OK"
    assert by["game logs"].status == "FAIL" and by["smoke prediction"].status == "FAIL"
    assert by["calibration artifacts"].status == "WARN" and by["schedule"].status == "WARN"
    text = render_checks(checks)
    assert "NOT READY" in text and "make data" in text


def test_readiness_on_fixture_database(populated: tuple[str, Session]) -> None:
    _, s = populated
    checks = run_checks(
        s,
        calibration_dir=Path("artifacts/calibration"),
        current_season_start=2024,
        now=datetime(2024, 12, 2, tzinfo=UTC),
    )
    by = {c.name: c for c in checks}
    assert by["season 2024-25 (current)"].status == "OK"
    assert by["season 2023-24"].status == "FAIL"  # completed season absent from the fixture
    assert by["smoke prediction"].status == "OK" and "proj" in by["smoke prediction"].detail
    assert (
        by["calibration artifacts"].status == "OK"
        and "3 loaded" in by["calibration artifacts"].detail
    )
    assert by["forecast snapshots"].detail == "3 stored"
    assert "NOT READY" in render_checks(checks)


def test_cli_check_exit_codes(
    tmp_path: Path, synthetic_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'c.db'}"
    main(["--database-url", url, "init-db"])
    assert main(["--database-url", url, "check"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "NOT READY" in out


# --- backup / restore ------------------------------------------------------------------
def test_backup_and_restore_roundtrip(populated: tuple[str, Session], tmp_path: Path) -> None:
    _, s = populated
    engine = s.get_bind()
    assert hasattr(engine, "url")
    backup = backup_database(engine, tmp_path / "backups" / "b.sqlite")  # type: ignore[arg-type]
    assert backup.is_file() and not backup.with_suffix(".sqlite.part").exists()
    with sqlite3.connect(backup) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("SELECT count(*) FROM prediction_snapshots").fetchone()[0] == 3
    # restore into a new location
    target_engine = create_db_engine(f"sqlite:///{tmp_path / 'restored.db'}")
    restore_database(target_engine, backup)
    with get_session_factory(target_engine)() as t:
        assert snapshot_counts(t) == (3, 3)
        snap = t.scalar(select(PredictionSnapshot).limit(1))
        assert snap is not None
        snap.projection = 0.0
        with pytest.raises(Exception, match="immutable"):
            t.flush()
        t.rollback()
    # refuses to overwrite a populated database without --force, then keeps a safety copy with it
    with pytest.raises(PortabilityError, match="--force"):
        restore_database(target_engine, backup)
    restore_database(target_engine, backup, force=True)
    assert list((tmp_path).glob("restored.pre-restore-*.db"))


def test_restore_rejects_bad_files(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 't.db'}")
    with pytest.raises(PortabilityError, match="not found"):
        restore_database(engine, tmp_path / "missing.sqlite")
    other = tmp_path / "other.sqlite"
    with sqlite3.connect(other) as c:
        c.execute("CREATE TABLE x (a int)")
    with pytest.raises(PortabilityError, match="not a PrizePicks"):
        restore_database(engine, other)
    mem = create_db_engine("sqlite:///:memory:")
    with pytest.raises(PortabilityError, match="file-based"):
        backup_database(mem, tmp_path / "m.sqlite")


def test_cli_backup_restore(
    populated: tuple[str, Session], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url, _ = populated
    out = tmp_path / "cli-backup.sqlite"
    assert main(["--database-url", url, "backup", "--out", str(out)]) == 0
    assert "backup written" in capsys.readouterr().out and out.is_file()
    new_url = f"sqlite:///{tmp_path / 'new.db'}"
    assert main(["--database-url", new_url, "restore", "--from", str(out)]) == 0
    assert main(["--database-url", new_url, "restore", "--from", str(out)]) == 2  # populated now
    assert "--force" in capsys.readouterr().err


# --- snapshot export / import -------------------------------------------------------------
def test_export_import_snapshots_by_external_ids(
    populated: tuple[str, Session], tmp_path: Path, synthetic_csv: Path
) -> None:
    _, s = populated
    out = tmp_path / "snaps.csv.gz"
    res = export_snapshots(s, out)
    assert res.exported == 3
    with gzip.open(out, "rt") as fh:
        header = fh.readline()
    assert "player_external_id" in header and "outcome_status" in header
    # fresh database rebuilt from the same public data (different internal ids possible)
    engine = create_db_engine(f"sqlite:///{tmp_path / 'rebuilt.db'}")
    init_db(engine)
    with get_session_factory(engine)() as t:
        load_game_logs(t, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
        first = import_snapshots(t, out)
        assert first.imported == 3 and first.outcomes_imported == 3 and first.skipped_existing == 0
        again = import_snapshots(t, out)
        assert again.imported == 0 and again.skipped_existing == 3
        assert snapshot_counts(t) == (3, 3)
        snap = t.scalar(
            select(PredictionSnapshot).where(PredictionSnapshot.stat_type == "rebounds")
        )
        assert (
            snap is not None and snap.line == 8.0 and snap.cutoff_at == datetime(2024, 11, 30, 12)
        )
        outcome = t.scalar(
            select(PredictionOutcome)
            .join(PredictionSnapshot)
            .where(PredictionSnapshot.stat_type == "rebounds")
        )
        assert (
            outcome is not None
            and outcome.status == "evaluated"
            and outcome.actual == 8
            and outcome.outcome_push is True
        )
    # import into a database without those players: skipped, not crashed
    bare = create_db_engine(f"sqlite:///{tmp_path / 'bare.db'}")
    init_db(bare)
    with get_session_factory(bare)() as b:
        res_b = import_snapshots(b, out)
        assert res_b.imported == 0 and res_b.skipped_unknown_player == 3
