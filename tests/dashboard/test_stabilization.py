"""Phase 4A.1 regression tests: DB-level immutability, status filter, error states, run default."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from prizepicks_predictor.cli import main
from prizepicks_predictor.dashboard import data
from prizepicks_predictor.db import (
    PredictionSnapshot,
    create_db_engine,
    init_db,
)
from prizepicks_predictor.prediction.service import resolve_calibration_dir
from prizepicks_predictor.services.evaluation import evaluate_predictions

from .conftest import TARGET


def test_raw_sql_cannot_mutate_or_delete_snapshots(with_forecasts: Session) -> None:
    snap_id = with_forecasts.scalar(select(PredictionSnapshot.id).limit(1))
    with pytest.raises(IntegrityError, match="immutable"):
        with_forecasts.execute(
            text("UPDATE prediction_snapshots SET projection = 0 WHERE id = :i"), {"i": snap_id}
        )
    with_forecasts.rollback()
    with pytest.raises(IntegrityError, match="immutable"):
        with_forecasts.execute(
            text("DELETE FROM prediction_snapshots WHERE id = :i"), {"i": snap_id}
        )
    with_forecasts.rollback()
    assert with_forecasts.get(PredictionSnapshot, snap_id) is not None


def test_init_db_installs_triggers_on_existing_database(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER trg_prediction_snapshots_no_update"))
    init_db(engine)  # idempotent re-run restores it
    with engine.connect() as conn:
        names = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
        }
    assert {"trg_prediction_snapshots_no_update", "trg_prediction_snapshots_no_delete"} <= names


def test_status_filter_applies_before_limit(with_forecasts: Session) -> None:
    evaluate_predictions(with_forecasts, through=TARGET, now=datetime(2024, 12, 3, tzinfo=UTC))
    # 12 missing_result (12-01) + 1 evaluated (11-20); with limit=1 the filter must still find it
    assert len(data.list_snapshots(with_forecasts, status="evaluated", limit=1)) == 1
    assert len(data.list_snapshots(with_forecasts, status="pending")) == 0
    assert len(data.list_snapshots(with_forecasts, status="missing_result", limit=5)) == 5


def test_resolve_calibration_dir_falls_back_to_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # cwd without artifacts/
    resolved = resolve_calibration_dir("artifacts/calibration")
    assert resolved.is_absolute() and resolved.is_dir()
    assert resolve_calibration_dir(str(tmp_path)) == tmp_path
    assert resolve_calibration_dir("definitely/missing") == Path("definitely/missing")


def _app_test(url: str, monkeypatch: pytest.MonkeyPatch, page: str) -> AppTest:
    monkeypatch.setenv("PPP_DATABASE_URL", url)
    from prizepicks_predictor.config import get_settings
    from prizepicks_predictor.dashboard.common import _factory

    get_settings.cache_clear()
    _factory.clear()
    script = (
        "from prizepicks_predictor.dashboard.common import guarded\n"
        f"from prizepicks_predictor.dashboard.pages import {page}\n"
        f"guarded({page})()\n"
    )
    return AppTest.from_string(script, default_timeout=90).run()


def test_database_connection_failure_shows_error_not_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _app_test("sqlite:////proc/nonexistent/dir/x.db", monkeypatch, "overview_page")
    assert not at.exception
    assert any("Database unavailable" in e.value for e in at.error)


def test_empty_database_pages_render_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    main(["--database-url", url, "init-db"])
    for page in (
        "overview_page",
        "players_page",
        "predict_page",
        "forecasts_page",
        "performance_page",
    ):
        at = _app_test(url, monkeypatch, page)
        assert not at.exception, page
    at = _app_test(url, monkeypatch, "overview_page")
    assert any("No game logs loaded" in w.value for w in at.warning)
    assert any("No schedule loaded" in w.value for w in at.warning)
    at = _app_test(url, monkeypatch, "players_page")
    at.text_input[0].input("jokic").run()
    assert any("No player matches" in i.value for i in at.info)


def test_performance_default_run_is_held_out_not_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_csv: Path
) -> None:
    url = f"sqlite:///{tmp_path / 'p.db'}"
    main(["--database-url", url, "init-db"])
    at = _app_test(url, monkeypatch, "performance_page")
    assert not at.exception
    assert "held-out test" in at.selectbox[0].value
    assert not any("smoke run" in w.value for w in at.warning)


def test_snapshot_kind_never_prospective_after_tipoff() -> None:
    tip = datetime(2026, 11, 5, 0, 0)
    assert data.snapshot_kind(datetime(2026, 11, 4, 23, 59), tip) == "prospective"
    assert data.snapshot_kind(tip, tip) == "unverified"
    assert data.snapshot_kind(datetime(2026, 11, 5, 0, 1), tip) == "unverified"
