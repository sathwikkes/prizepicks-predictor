from __future__ import annotations

from datetime import date

import pytest

from prizepicks_predictor.prediction.features import build_features
from prizepicks_predictor.prediction.models import (
    DEFAULT_MODEL,
    MODELS,
    EwmaModel,
    MinutesAdjustedModel,
    ModelInputError,
    RollingAverageModel,
    SeasonAverageModel,
    get_model,
)
from prizepicks_predictor.prediction.schemas import FeatureSet, StatType
from tests.prediction.conftest import series


@pytest.fixture
def features(as_of: date) -> FeatureSet:
    hist = series(date(2024, 11, 1), [10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32], minutes=30.0)
    return build_features(hist, stat=StatType.POINTS, as_of_date=as_of)


def test_season_average_model(features: FeatureSet) -> None:
    out = SeasonAverageModel().project(features)
    assert out.name == "season_average"
    assert out.projection == pytest.approx(21.0)
    assert any("12 games" in a for a in out.assumptions)


def test_rolling_models(features: FeatureSet) -> None:
    assert RollingAverageModel(5).project(features).projection == pytest.approx(28.0)
    assert RollingAverageModel(10).project(features).projection == pytest.approx(23.0)
    with pytest.raises(ValueError):
        RollingAverageModel(7)


def test_ewma_model(features: FeatureSet) -> None:
    out = EwmaModel().project(features)
    assert out.projection == pytest.approx(features.ewma_mean or 0)
    assert 23.0 < out.projection < 32.0  # between last-10 mean and max


def test_minutes_adjusted_model(features: FeatureSet) -> None:
    out = MinutesAdjustedModel().project(features)
    # constant 30 minutes => projected minutes 30, rate = last10 mean / 30
    assert out.projected_minutes == pytest.approx(30.0)
    assert out.projection == pytest.approx(23.0)
    assert any("linearly" in a for a in out.assumptions)


def test_minutes_adjusted_responds_to_minutes_change(as_of: date) -> None:
    # 1 pt/min; recent minutes 20, older minutes 40 -> blend 0.6*20 + 0.4*30 = 24
    hist = series(date(2024, 11, 1), [40, 40, 40, 40, 40, 20, 20, 20, 20, 20])
    hist = [r.model_copy(update={"minutes": float(r.points)}) for r in hist]
    f = build_features(hist, stat=StatType.POINTS, as_of_date=as_of)
    out = MinutesAdjustedModel().project(f)
    assert out.projected_minutes == pytest.approx(24.0)
    assert out.projection == pytest.approx(24.0)


def test_registry_and_default() -> None:
    assert set(MODELS) == {
        "season_average",
        "rolling_5",
        "rolling_10",
        "ewma",
        "ewma_schedule",
        "ewma_opponent",
        "minutes_adjusted",
    }
    assert DEFAULT_MODEL in MODELS
    assert get_model("rolling_5").name == "rolling_5"
    with pytest.raises(KeyError, match="unknown model"):
        get_model("xgboost")


def test_model_input_error_on_missing_features(as_of: date) -> None:
    empty = build_features([], stat=StatType.POINTS, as_of_date=as_of)
    for model in MODELS.values():
        with pytest.raises(ModelInputError):
            model.project(empty)
