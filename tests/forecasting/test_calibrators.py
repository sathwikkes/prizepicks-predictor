from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from prizepicks_predictor.prediction import PredictionRequest, StatType, WarningCode, predict
from prizepicks_predictor.prediction.calibrators import CalibrationArtifact, CalibratorRegistry
from prizepicks_predictor.prediction.schemas import DistributionFamily, OutcomeProbabilities
from prizepicks_predictor.services import find_players
from tests.forecasting.conftest import make_artifact


def test_artifact_roundtrip_and_lookup(tmp_path: Path) -> None:
    art = make_artifact()
    path = art.save(tmp_path)
    loaded = CalibrationArtifact.load(path)
    assert loaded == art
    reg = CalibratorRegistry.load(tmp_path)
    assert len(reg) == 1
    assert (
        reg.find(
            stat=StatType.POINTS,
            model_name="ewma",
            model_version="1.0",
            family=DistributionFamily.AUTO,
        )
        is art
        or loaded
    )
    assert (
        reg.find(
            stat=StatType.REBOUNDS,
            model_name="ewma",
            model_version="1.0",
            family=DistributionFamily.AUTO,
        )
        is None
    )
    assert (
        reg.find(
            stat=StatType.POINTS,
            model_name="rolling_5",
            model_version="1.0",
            family=DistributionFamily.AUTO,
        )
        is None
    )
    assert (
        reg.find(
            stat=StatType.POINTS,
            model_name="ewma",
            model_version="2.0",
            family=DistributionFamily.AUTO,
        )
        is None
    )
    assert (
        reg.find(
            stat=StatType.POINTS,
            model_name="ewma",
            model_version="1.0",
            family=DistributionFamily.NORMAL,
        )
        is None
    )


def test_artifact_validation_rejects_bad_payloads(tmp_path: Path) -> None:
    base = json.loads(make_artifact().model_dump_json())
    bad_version = {**base, "schema_version": 99}
    with pytest.raises(ValueError, match="schema_version"):
        CalibrationArtifact.model_validate(bad_version)
    with pytest.raises(ValueError, match="non-decreasing"):
        CalibrationArtifact.model_validate({**base, "y": [0.5, 0.4, 0.6, 0.7, 0.8]})
    with pytest.raises(ValueError, match="equal-length"):
        CalibrationArtifact.model_validate({**base, "x": [0.5]})
    (tmp_path / "broken.json").write_text("{not json")
    with pytest.raises(ValueError, match="cannot read"):
        CalibrationArtifact.load(tmp_path / "broken.json")
    assert len(CalibratorRegistry.load(tmp_path / "missing")) == 0


def test_apply_preserves_push_and_normalisation() -> None:
    art = make_artifact()
    raw = OutcomeProbabilities(more=0.54, less=0.36, push=0.10)
    cal = art.apply(raw)
    assert cal.push == pytest.approx(0.10)
    assert cal.more + cal.less + cal.push == pytest.approx(1.0)
    # conditional P(more|no push)=0.6 -> block value 0.55, scaled by non-push mass 0.9
    assert cal.more == pytest.approx(0.55 * 0.9)
    assert cal.less == pytest.approx(0.45 * 0.9)
    half = OutcomeProbabilities(more=0.9, less=0.1, push=0.0)
    assert art.apply(half).push == 0.0
    assert art.calibrate_conditional(0.0) == pytest.approx(0.3) and art.calibrate_conditional(
        1.0
    ) == pytest.approx(0.8)


def test_platt_artifact_apply() -> None:
    art = make_artifact().model_copy(
        update={"method": "platt", "a": 0.5, "b": 0.0, "x": [], "y": []}
    )
    assert art.calibrate_conditional(0.5) == pytest.approx(0.5)
    assert 0.5 < art.calibrate_conditional(0.9) < 0.9  # shrunk toward 0.5


def _req(pid: int, stat: StatType, line: float, **kw: object) -> PredictionRequest:
    return PredictionRequest(
        player_id=pid, stat_type=stat, line=line, as_of_date=date(2024, 12, 1), **kw
    )  # type: ignore[arg-type]


def test_predict_attaches_calibrated_probabilities(
    loaded: Session, registry: CalibratorRegistry
) -> None:
    pid = find_players(loaded, "Alex Rivera")[0].id
    res = predict(loaded, _req(pid, StatType.POINTS, 24.5), calibrators=registry)
    assert res.probabilities_calibrated is not None and res.calibration is not None
    assert res.calibration.artifact_id == "test.points"
    assert res.calibration_status.startswith("isotonic (in_sample_only")
    assert res.probabilities_calibrated.more + res.probabilities_calibrated.less == pytest.approx(
        1.0
    )
    assert res.probabilities_calibrated != res.probabilities  # artifact is not the identity
    assert WarningCode.CALIBRATION_UNAVAILABLE not in res.warnings
    # raw probabilities untouched
    assert (
        res.probabilities.more + res.probabilities.less + res.probabilities.push
        == pytest.approx(1.0)
    )


def test_predict_integer_line_keeps_push(loaded: Session, registry: CalibratorRegistry) -> None:
    pid = find_players(loaded, "Alex Rivera")[0].id
    res = predict(loaded, _req(pid, StatType.REBOUNDS, 8.0), calibrators=registry)
    assert res.probabilities.push > 0
    cal = res.probabilities_calibrated
    assert cal is not None and cal.push == pytest.approx(res.probabilities.push)
    assert cal.more + cal.less + cal.push == pytest.approx(1.0)
    # the artifact was fitted on half-point lines: say so at integer lines
    assert WarningCode.CALIBRATION_INTEGER_LINE_UNVALIDATED in res.warnings
    half = predict(loaded, _req(pid, StatType.REBOUNDS, 8.5), calibrators=registry)
    assert WarningCode.CALIBRATION_INTEGER_LINE_UNVALIDATED not in half.warnings


def test_predict_fallback_when_no_matching_artifact(
    loaded: Session, registry: CalibratorRegistry
) -> None:
    pid = find_players(loaded, "Alex Rivera")[0].id
    other_model = predict(
        loaded, _req(pid, StatType.POINTS, 24.5, model_name="rolling_10"), calibrators=registry
    )
    assert other_model.probabilities_calibrated is None
    assert WarningCode.CALIBRATION_UNAVAILABLE in other_model.warnings
    assert "no artifact for points/rolling_10-1.0/auto" in other_model.calibration_status
    other_family = predict(
        loaded,
        _req(pid, StatType.POINTS, 24.5, distribution=DistributionFamily.NORMAL),
        calibrators=registry,
    )
    assert other_family.probabilities_calibrated is None
    disabled = predict(
        loaded, _req(pid, StatType.POINTS, 24.5, calibrate=False), calibrators=registry
    )
    assert disabled.probabilities_calibrated is None and "disabled" in disabled.calibration_status
    assert WarningCode.CALIBRATION_UNAVAILABLE not in disabled.warnings
    empty = predict(
        loaded, _req(pid, StatType.POINTS, 24.5), calibrators=CalibratorRegistry.empty()
    )
    assert empty.probabilities_calibrated is None


def test_version_mismatch_is_not_applied(loaded: Session) -> None:
    pid = find_players(loaded, "Alex Rivera")[0].id
    stale = CalibratorRegistry([make_artifact(model_version="0.9")])
    res = predict(loaded, _req(pid, StatType.POINTS, 24.5), calibrators=stale)
    assert res.probabilities_calibrated is None
    assert WarningCode.CALIBRATION_UNAVAILABLE in res.warnings


def test_default_registry_follows_settings_dir(
    loaded: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prizepicks_predictor.config import get_settings
    from prizepicks_predictor.prediction.service import _registry_for

    art_dir = tmp_path / "arts"
    make_artifact().save(art_dir)
    monkeypatch.setenv("PPP_CALIBRATION_DIR", str(art_dir))
    get_settings.cache_clear()
    _registry_for.cache_clear()
    pid = find_players(loaded, "Alex Rivera")[0].id
    res = predict(loaded, _req(pid, StatType.POINTS, 24.5))  # no explicit registry
    assert res.calibration is not None and res.calibration.artifact_id == "test.points"
