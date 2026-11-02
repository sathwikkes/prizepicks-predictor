from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from prizepicks_predictor.db import create_db_engine, get_session_factory
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction import (
    DistributionFamily,
    InsufficientDataError,
    PredictionRequest,
    StatType,
    UnknownModelError,
    WarningCode,
    predict,
)
from prizepicks_predictor.prediction.models import MODELS
from prizepicks_predictor.prediction.service import DataAccessError
from prizepicks_predictor.services import PlayerNotFoundError, find_players


@pytest.fixture
def loaded(session: Session, synthetic_csv: Path) -> Session:
    load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    return session


def _rivera(loaded: Session) -> int:
    return find_players(loaded, "Alex Rivera")[0].id


def test_full_prediction_on_synthetic_fixture(loaded: Session) -> None:
    req = PredictionRequest(
        player_id=_rivera(loaded),
        stat_type=StatType.POINTS,
        line=24.5,
        as_of_date=date(2024, 12, 1),
    )
    res = predict(loaded, req)
    assert res.player_name == "Alex Rivera" and res.team == "BOS"
    assert res.sample_size == 12 and res.features.season_games == 12
    assert res.model.name == "ewma"
    assert (
        res.probabilities.more + res.probabilities.less + res.probabilities.push
        == pytest.approx(1.0)
    )
    assert res.probabilities.push == 0.0
    assert res.interval_80[0] <= res.model.projection <= res.interval_80[1]
    assert res.calibration_status == "unvalidated"
    assert res.features.cutoff_last_game_date is not None
    assert res.features.cutoff_last_game_date < req.as_of_date
    assert "opponent_defensive_rating" in res.unavailable_features
    assert WarningCode.SMALL_SAMPLE not in res.warnings


def test_integer_line_has_push_probability(loaded: Session) -> None:
    req = PredictionRequest(
        player_id=_rivera(loaded), stat_type=StatType.REBOUNDS, line=8, as_of_date=date(2024, 12, 1)
    )
    res = predict(loaded, req)
    assert res.probabilities.push > 0


@pytest.mark.parametrize("model_name", sorted(MODELS))
def test_every_model_runs(loaded: Session, model_name: str) -> None:
    req = PredictionRequest(
        player_id=_rivera(loaded),
        stat_type=StatType.ASSISTS,
        line=4.5,
        as_of_date=date(2024, 12, 1),
        model_name=model_name,
    )
    res = predict(loaded, req)
    assert res.model.name == model_name
    assert res.model.projection > 0
    assert res.model.assumptions


@pytest.mark.parametrize("family", list(DistributionFamily))
def test_every_distribution_runs(loaded: Session, family: DistributionFamily) -> None:
    req = PredictionRequest(
        player_id=_rivera(loaded),
        stat_type=StatType.POINTS,
        line=22.5,
        as_of_date=date(2024, 12, 1),
        distribution=family,
    )
    res = predict(loaded, req)
    if family is DistributionFamily.EMPIRICAL:
        # only 12 games: empirical falls back and says so
        assert res.distribution.family is not DistributionFamily.EMPIRICAL
        assert WarningCode.EMPIRICAL_FALLBACK in res.warnings
    elif family is not DistributionFamily.AUTO:
        assert res.distribution.family is family


def test_small_sample_warning_and_insufficient_data(loaded: Session) -> None:
    pid = _rivera(loaded)
    # fixture's first games: 10-26, 10-28, ... -> 3 games before 11-02
    small = predict(
        loaded,
        PredictionRequest(
            player_id=pid, stat_type=StatType.POINTS, line=20.5, as_of_date=date(2024, 11, 2)
        ),
    )
    assert small.sample_size == 3 and WarningCode.SMALL_SAMPLE in small.warnings

    with pytest.raises(InsufficientDataError) as info:
        predict(
            loaded,
            PredictionRequest(
                player_id=pid, stat_type=StatType.POINTS, line=20.5, as_of_date=date(2024, 10, 29)
            ),
        )
    assert info.value.games_available == 2


def test_unknown_player(loaded: Session) -> None:
    with pytest.raises(PlayerNotFoundError):
        predict(
            loaded,
            PredictionRequest(
                player_id=9999, stat_type=StatType.POINTS, line=20.5, as_of_date=date(2024, 12, 1)
            ),
        )


def test_unknown_model(loaded: Session) -> None:
    with pytest.raises(UnknownModelError):
        predict(
            loaded,
            PredictionRequest(
                player_id=_rivera(loaded),
                stat_type=StatType.POINTS,
                line=20.5,
                as_of_date=date(2024, 12, 1),
                model_name="xgboost",
            ),
        )


@pytest.mark.parametrize("line", [24.3, 0, -5, 150.5])
def test_invalid_lines_rejected(line: float) -> None:
    with pytest.raises(ValidationError):
        PredictionRequest(
            player_id=1, stat_type=StatType.POINTS, line=line, as_of_date=date(2024, 12, 1)
        )


def test_stat_specific_line_cap() -> None:
    PredictionRequest(
        player_id=1, stat_type=StatType.POINTS, line=45.5, as_of_date=date(2024, 12, 1)
    )
    with pytest.raises(ValidationError):
        PredictionRequest(
            player_id=1, stat_type=StatType.REBOUNDS, line=45.5, as_of_date=date(2024, 12, 1)
        )


def test_unsupported_stat_and_missing_date() -> None:
    with pytest.raises(ValueError):
        StatType("steals")
    with pytest.raises(ValidationError):
        PredictionRequest.model_validate({"player_id": 1, "stat_type": "points", "line": 20.5})


def test_database_failure_is_data_access_error(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'empty.db'}")  # no init_db -> no tables
    with get_session_factory(engine)() as bare, pytest.raises(DataAccessError):
        predict(
            bare,
            PredictionRequest(
                player_id=1, stat_type=StatType.POINTS, line=20.5, as_of_date=date(2024, 12, 1)
            ),
        )


def test_prediction_is_deterministic(loaded: Session) -> None:
    req = PredictionRequest(
        player_id=_rivera(loaded),
        stat_type=StatType.POINTS,
        line=24.5,
        as_of_date=date(2024, 12, 1),
    )
    a, b = predict(loaded, req), predict(loaded, req)
    assert a.model == b.model and a.probabilities == b.probabilities and a.features == b.features
