"""Prediction orchestration: history -> features -> model -> distribution -> probabilities."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from prizepicks_predictor.prediction.distributions import (
    build_predictive,
    outcome_probabilities,
    predictive_variance,
)
from prizepicks_predictor.prediction.features import build_features, stat_value
from prizepicks_predictor.prediction.models import ModelInputError, get_model
from prizepicks_predictor.prediction.schemas import (
    PredictionRequest,
    PredictionResult,
    WarningCode,
)
from prizepicks_predictor.services.game_logs import PlayerNotFoundError, resolve_player
from prizepicks_predictor.services.game_logs import get_player_game_logs as _game_logs

SMALL_SAMPLE_GAMES = 10
#: Games used to estimate outcome variance. Long enough to be stable, short
#: enough to track role changes.
VARIANCE_WINDOW = 20


class PredictionError(Exception):
    """Base class for prediction failures the caller should handle."""


class InsufficientDataError(PredictionError):
    def __init__(self, message: str, *, games_available: int) -> None:
        super().__init__(message)
        self.games_available = games_available


class UnknownModelError(PredictionError):
    pass


class DataAccessError(PredictionError):
    pass


def predict(session: Session, request: PredictionRequest) -> PredictionResult:
    """Produce a More/Less/Push prediction for ``request``.

    Raises ``PlayerNotFoundError``, ``UnknownModelError``, ``InsufficientDataError``
    or ``DataAccessError``. Line and stat validation happens in ``PredictionRequest``.
    """
    try:
        model = get_model(request.model_name)
    except KeyError as exc:
        raise UnknownModelError(str(exc)) from exc

    try:
        player = resolve_player(session, request.player_id)
        history = _game_logs(session, player.id, before=request.as_of_date)
    except PlayerNotFoundError:
        raise
    except SQLAlchemyError as exc:
        raise DataAccessError(f"database error while loading history: {exc}") from exc

    features = build_features(history, stat=request.stat_type, as_of_date=request.as_of_date)
    if features.games_total < model.min_games:
        raise InsufficientDataError(
            f"{player.full_name} has {features.games_total} games with minutes before "
            f"{request.as_of_date}; model {model.name!r} needs at least {model.min_games}",
            games_available=features.games_total,
        )

    try:
        output = model.project(features)
    except ModelInputError as exc:
        raise InsufficientDataError(str(exc), games_available=features.games_total) from exc

    played = [r for r in history if r.minutes > 0]
    recent_values = [float(stat_value(r, request.stat_type)) for r in played[:VARIANCE_WINDOW]]
    var_est = predictive_variance(recent_values, mean=output.projection)
    predictive = build_predictive(
        request.distribution,
        mean=output.projection,
        variance=var_est.variance,
        history_values=recent_values,
    )
    probabilities = outcome_probabilities(predictive, request.line)

    warnings = list(features.warnings)
    if features.games_total < SMALL_SAMPLE_GAMES:
        warnings.insert(0, WarningCode.SMALL_SAMPLE)
    if any("fell back" in note for note in predictive.notes):
        warnings.append(WarningCode.EMPIRICAL_FALLBACK)

    return PredictionResult(
        player_id=player.id,
        player_name=player.full_name,
        team=features.latest_team,
        stat_type=request.stat_type,
        line=request.line,
        as_of_date=request.as_of_date,
        model=output,
        distribution=predictive.summary(var_est.sample_size),
        probabilities=probabilities,
        interval_80=(predictive.quantile(0.10), predictive.quantile(0.90)),
        sample_size=features.games_total,
        features=features,
        warnings=warnings,
        unavailable_features=list(features.unavailable),
        predicted_at=datetime.now(UTC),
    )
