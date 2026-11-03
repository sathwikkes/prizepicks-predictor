"""Phase 4C regressions: forecasts page on the real snapshot export, calibration support."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from prizepicks_predictor.cli import main
from prizepicks_predictor.dashboard import data
from prizepicks_predictor.db import (
    PredictionSnapshot,
    create_db_engine,
    get_session_factory,
    init_db,
)
from prizepicks_predictor.prediction import PredictionRequest, StatType, WarningCode, predict
from prizepicks_predictor.prediction.calibrators import CalibrationArtifact, CalibratorRegistry
from prizepicks_predictor.services import find_players
from prizepicks_predictor.services.portability import import_snapshots
from tests.forecasting.conftest import make_artifact

EXPORT = (
    Path(__file__).resolve().parents[2] / "artifacts" / "snapshots" / "snapshots-20261103.csv.gz"
)


@pytest.fixture
def real_snapshots_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Fresh temporary database holding the committed real snapshot export."""
    url = f"sqlite:///{tmp_path / 'snaps.db'}"
    engine = create_db_engine(url)
    init_db(engine)
    s = get_session_factory(engine)()
    res = import_snapshots(s, EXPORT, create_missing_players=True)
    assert res.imported == 1227 and res.players_created > 0 and res.skipped_unknown_player == 0
    monkeypatch.setenv("PPP_DATABASE_URL", url)
    from prizepicks_predictor.config import get_settings
    from prizepicks_predictor.dashboard.common import _factory

    get_settings.cache_clear()
    _factory.clear()
    return s


def test_import_real_export_schema(real_snapshots_db: Session) -> None:
    s = real_snapshots_db
    stats = data.snapshot_stats(s, target_date=date(2026, 11, 4))
    assert stats.snapshots == 1191 and stats.unique_player_game_stat == 1191
    assert stats.players == 397 and stats.stats == 3
    assert stats.unverified == 0 and stats.prospective == 1191  # cutoffs precede tip-offs
    replay = data.snapshot_stats(s, target_date=date(2026, 3, 1))
    assert replay.snapshots == 36 and replay.unverified == 36
    snap = s.scalar(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.target_date == date(2026, 11, 4))
        .limit(1)
    )
    assert (
        snap is not None
        and snap.model_name == "ewma"
        and snap.calibration_artifact
        and snap.cutoff_at < snap.tipoff_utc
    )  # type: ignore[operator]


def test_list_snapshots_filters_on_real_export(real_snapshots_db: Session) -> None:
    s = real_snapshots_db
    assert len(data.list_snapshots(s, limit=5000)) == 1227
    nov4 = data.list_snapshots(s, target_date=date(2026, 11, 4), limit=5000)
    assert (
        len(nov4) == 1191
        and {r.kind for r in nov4} == {"prospective"}
        and {r.status for r in nov4} == {"pending"}
    )
    assert (
        len(data.list_snapshots(s, target_date=date(2026, 11, 4), stat=StatType.POINTS, limit=5000))
        == 397
    )
    assert len(data.list_snapshots(s, status="evaluated", limit=5000)) == 24
    assert len(data.list_snapshots(s, status="missing_result", limit=5000)) == 12
    jokic = data.list_snapshots(s, player_query="jokic", limit=5000)
    assert jokic and all("Jokić" in r.player_name for r in jokic)
    assert data.list_snapshots(s, player_query="zzzz", limit=5000) == []
    # filters act on the full set before the display limit
    assert (
        len(data.list_snapshots(s, target_date=date(2026, 3, 1), status="evaluated", limit=1)) == 1
    )


def test_forecasts_page_renders_real_snapshots_with_filters(real_snapshots_db: Session) -> None:
    script = "from prizepicks_predictor.dashboard.pages import forecasts_page\nforecasts_page()\n"
    at = AppTest.from_string(script, default_timeout=180).run()
    assert not at.exception
    at.selectbox[0].select(next(o for o in at.selectbox[0].options if "2026-11-04" in o)).run()
    assert not at.exception, [e.message for e in at.exception]
    caption = " ".join(c.value for c in at.caption)
    assert "1191 rows = 1191 unique" in caption and "397 players" in caption
    frame = at.dataframe[0].value
    cols = " ".join(frame.columns)
    assert len(frame) == 1191 and "cutoff (UTC)" in cols and "tip-off (UTC)" in cols
    # stat filter
    stat_box = next(sb for sb in at.selectbox if sb.label == "Statistic")
    stat_box.select("assists").run()
    assert not at.exception and len(at.dataframe[0].value) == 397
    # status filter to an empty set shows a message, not an error
    status_box = next(sb for sb in at.selectbox if sb.label == "Status")
    status_box.select("evaluated").run()
    assert not at.exception and any("No snapshots match" in i.value for i in at.info)
    # timezone selector changes the displayed timestamp text but not the data
    tz_box = next(sb for sb in at.selectbox if sb.label.startswith("Display time zone"))
    tz_box.select("America/New_York").run()
    assert not at.exception


def test_localize_display_only() -> None:
    from datetime import datetime

    assert data.localize(datetime(2026, 11, 5, 0, 0), "UTC") == "2026-11-05 00:00 UTC"
    assert data.localize(datetime(2026, 11, 5, 0, 0), "America/New_York") == "2026-11-04 19:00 EST"
    assert data.localize(None, "UTC") == ""


# --- calibration support ---------------------------------------------------------------
def _art(**kw: object) -> CalibrationArtifact:
    base = make_artifact()
    return base.model_copy(
        update={
            "support_lo": 0.05,
            "support_hi": 0.93,
            "support_bins": [0] * 1 + [500] * 17 + [150, 0],
            **kw,
        }
    )


def test_support_status_rules() -> None:
    art = _art()
    assert art.support_status(0.5).status == "supported"
    assert art.support_status(0.97).status == "outside_range"
    assert art.support_status(0.01).status == "outside_range"
    ins = art.support_status(0.92)  # inside range but bin 18 has 150 < 200
    assert ins.status == "insufficient_evidence" and ins.bin_count == 150
    assert make_artifact().support_status(0.5).status == "unknown"
    assert art.with_support([0.2, 0.4, 0.6]).artifact_id == art.artifact_id


def _req(pid: int, stat: StatType, line: float, **kw: object) -> PredictionRequest:
    return PredictionRequest(
        player_id=pid, stat_type=stat, line=line, as_of_date=date(2024, 12, 1), **kw
    )  # type: ignore[arg-type]


def test_extreme_thresholds_fall_back_transparently(loaded: Session) -> None:
    pid = find_players(loaded, "Alex Rivera")[0].id
    reg = CalibratorRegistry([_art()])
    low = predict(loaded, _req(pid, StatType.POINTS, 5.5), calibrators=reg)  # raw P(more) ~ 1
    assert low.probabilities.more > 0.99
    assert low.probabilities_calibrated is None
    assert low.calibration_summary == "Calibration outside validated range"
    assert WarningCode.CALIBRATION_OUTSIDE_SUPPORT in low.warnings
    assert WarningCode.THRESHOLD_FAR_FROM_RECENT_MEDIAN in low.warnings
    high = predict(loaded, _req(pid, StatType.POINTS, 60.5), calibrators=reg)  # raw P(more) ~ 0
    assert high.probabilities.more < 0.01 and high.probabilities_calibrated is None
    assert high.calibration_summary == "Calibration outside validated range"
    mid = predict(loaded, _req(pid, StatType.POINTS, 23.5), calibrators=reg)
    assert (
        mid.probabilities_calibrated is not None
        and mid.calibration_summary == "Calibration supported"
    )
    assert mid.calibration is not None and mid.calibration.support_status == "supported"
    assert WarningCode.THRESHOLD_FAR_FROM_RECENT_MEDIAN not in mid.warnings
    q = mid.probabilities_calibrated
    assert 0 < q.more < 1 and q.more + q.less + q.push == pytest.approx(1.0)
    # integer line inside support: push preserved, extrapolation warning present
    whole = predict(
        loaded,
        _req(pid, StatType.REBOUNDS, 8.0),
        calibrators=CalibratorRegistry([_art(stat=StatType.REBOUNDS)]),
    )
    assert (
        whole.probabilities_calibrated is not None
        and whole.probabilities_calibrated.push == pytest.approx(whole.probabilities.push)
    )
    assert WarningCode.CALIBRATION_INTEGER_LINE_UNVALIDATED in whole.warnings
    # unknown support (no metadata) still applies and says so
    legacy = predict(
        loaded, _req(pid, StatType.POINTS, 23.5), calibrators=CalibratorRegistry([make_artifact()])
    )
    assert legacy.probabilities_calibrated is not None and legacy.calibration is not None
    assert legacy.calibration.support_status == "unknown"
    # no artifact at all
    none = predict(loaded, _req(pid, StatType.POINTS, 23.5), calibrators=CalibratorRegistry.empty())
    assert (
        none.calibration_summary == "Uncalibrated estimate shown"
        and none.probabilities_calibrated is None
    )


def test_insufficient_evidence_bin(loaded: Session) -> None:
    pid = find_players(loaded, "Alex Rivera")[0].id
    thin = _art(support_bins=[10] * 20, support_lo=0.0, support_hi=1.0)
    res = predict(loaded, _req(pid, StatType.POINTS, 23.5), calibrators=CalibratorRegistry([thin]))
    assert res.probabilities_calibrated is None
    assert res.calibration_summary == "Insufficient calibration evidence"
    assert WarningCode.CALIBRATION_INSUFFICIENT_EVIDENCE in res.warnings


def test_committed_artifacts_carry_support_and_reject_extremes() -> None:
    reg = CalibratorRegistry.load(Path("artifacts/calibration"))
    assert len(reg) == 3
    for art in reg.artifacts:
        assert art.support_bins and art.support_lo is not None and art.support_hi is not None
        assert art.support_status(0.999).status != "supported"
        assert art.support_status(0.5).status == "supported"


def test_format_probability_never_prints_certainty() -> None:
    from prizepicks_predictor.dashboard.labels import explain_warning, format_probability

    assert format_probability(1.0) == "> 0.999" and format_probability(0.0) == "< 0.001"
    assert format_probability(0.4567) == "0.457" and format_probability(None) == "n/a"
    assert "previous season" in explain_warning("window_crosses_season")
    assert explain_warning("made_up_code") == "made up code"


def test_cli_import_create_players_and_annotate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'c.db'}"
    main(["--database-url", url, "init-db"])
    assert (
        main(["--database-url", url, "import-snapshots", "--from", str(EXPORT), "--create-players"])
        == 0
    )
    assert "imported 1227 snapshots" in capsys.readouterr().out
