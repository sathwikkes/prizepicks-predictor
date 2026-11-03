"""Prediction orchestration: history -> features -> model -> distribution -> probabilities."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from prizepicks_predictor.config import get_settings
from prizepicks_predictor.prediction.calibrators import CalibratorRegistry
from prizepicks_predictor.prediction.distributions import (
    build_predictive,
    outcome_probabilities,
    predictive_variance,
)
from prizepicks_predictor.prediction.features import build_features, stat_value
from prizepicks_predictor.prediction.models import ModelInputError, get_model
from prizepicks_predictor.prediction.schedule_context import ScheduleContext, build_schedule_context
from prizepicks_predictor.prediction.schemas import (
    CalibrationInfo,
    PredictionRequest,
    PredictionResult,
    WarningCode,
)
from prizepicks_predictor.services.game_logs import PlayerNotFoundError, resolve_player
from prizepicks_predictor.services.game_logs import get_player_game_logs as _game_logs
from prizepicks_predictor.services.team_schedule import team_game_dates_before

SMALL_SAMPLE_GAMES = 10
#: Calibration lines were trailing-median +/- 2 (half-point); beyond this the threshold is
#: farther from the player's recent median than any validated line.
MAX_VALIDATED_OFFSET = 2.5
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


def resolve_calibration_dir(configured: str) -> Path:
    """A relative ``PPP_CALIBRATION_DIR`` is tried against the cwd, then the repository root."""
    path = Path(configured)
    if path.is_absolute() or path.is_dir():
        return path
    repo_root = Path(__file__).resolve().parents[3]
    candidate = repo_root / path
    return candidate if candidate.is_dir() else path


@lru_cache(maxsize=4)
def _registry_for(directory: str) -> CalibratorRegistry:
    return CalibratorRegistry.load(resolve_calibration_dir(directory))


def default_calibrators() -> CalibratorRegistry:
    """Artifacts from ``PPP_CALIBRATION_DIR`` (cached per directory)."""
    return _registry_for(get_settings().calibration_dir)


def schedule_context_for(
    session: Session, team: str | None, request: PredictionRequest
) -> ScheduleContext | None:
    """Team rest/venue context from completed games and the loaded schedule, all pre-target."""
    if team is None:
        return None
    dates, source = team_game_dates_before(session, team, request.as_of_date)
    return build_schedule_context(
        dates, target_date=request.as_of_date, is_home=request.is_home, source=source
    )


def predict(
    session: Session,
    request: PredictionRequest,
    *,
    calibrators: CalibratorRegistry | None = None,
) -> PredictionResult:
    """Produce a More/Less/Push prediction for ``request``.

    Raises ``PlayerNotFoundError``, ``UnknownModelError``, ``InsufficientDataError``
    or ``DataAccessError``. Line and stat validation happens in ``PredictionRequest``.

    ``calibrators`` defaults to the artifacts in the configured directory. A calibrated
    probability triple is attached only when an artifact matches the stat, model name,
    model version and requested family exactly; otherwise the result explains why not.
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
    features = features.model_copy(
        update={"schedule": schedule_context_for(session, features.latest_team, request)}
    )
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
    if features.schedule is None or features.schedule.rest_days is None:
        warnings.append(WarningCode.SCHEDULE_CONTEXT_UNAVAILABLE)
    if features.games_total < SMALL_SAMPLE_GAMES:
        warnings.insert(0, WarningCode.SMALL_SAMPLE)
    if any("fell back" in note for note in predictive.notes):
        warnings.append(WarningCode.EMPIRICAL_FALLBACK)

    calibrated = None
    info = None
    summary = "Uncalibrated estimate shown"
    recent_values = [float(stat_value(r, request.stat_type)) for r in played[:10]]
    line_offset = (request.line - statistics.median(recent_values)) if recent_values else None
    if line_offset is not None and abs(line_offset) > MAX_VALIDATED_OFFSET:
        warnings.append(WarningCode.THRESHOLD_FAR_FROM_RECENT_MEDIAN)
    if not request.calibrate:
        status = "uncalibrated: calibration disabled by request"
    else:
        registry = calibrators if calibrators is not None else default_calibrators()
        artifact = registry.find(
            stat=request.stat_type,
            model_name=output.name,
            model_version=output.version,
            family=request.distribution,
        )
        if artifact is None:
            status = (
                f"uncalibrated: no artifact for {request.stat_type.value}/{output.name}-"
                f"{output.version}/{request.distribution.value}"
            )
            warnings.append(WarningCode.CALIBRATION_UNAVAILABLE)
        else:
            non_push = probabilities.more + probabilities.less
            cond = probabilities.more / non_push if non_push > 0 else probabilities.more
            support = artifact.support_status(cond)
            held = artifact.held_out
            info = CalibrationInfo(
                artifact_id=artifact.artifact_id,
                method=artifact.method,
                fit_seasons=artifact.fit_seasons,
                n_fit=artifact.n_fit,
                validation_status=artifact.validation_status,
                line_policy=artifact.line_policy,
                held_out_ece_calibrated=held.ece_calibrated if held else None,
                held_out_ece_uncalibrated=held.ece_uncalibrated if held else None,
                support_status=support.status,
                support_message=support.message,
                evidence_count=support.bin_count,
            )
            if support.status == "outside_range":
                status = f"uncalibrated: {support.message}"
                summary = "Calibration outside validated range"
                warnings.append(WarningCode.CALIBRATION_OUTSIDE_SUPPORT)
            elif support.status == "insufficient_evidence":
                status = f"uncalibrated: {support.message}"
                summary = "Insufficient calibration evidence"
                warnings.append(WarningCode.CALIBRATION_INSUFFICIENT_EVIDENCE)
            else:
                calibrated = artifact.apply(probabilities)
                status = f"{artifact.method} ({artifact.validation_status}; synthetic lines only)"
                summary = "Calibration supported"
                if request.line_is_integer:
                    warnings.append(WarningCode.CALIBRATION_INTEGER_LINE_UNVALIDATED)

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
        probabilities_calibrated=calibrated,
        calibration=info,
        interval_80=(predictive.quantile(0.10), predictive.quantile(0.90)),
        sample_size=features.games_total,
        features=features,
        warnings=warnings,
        unavailable_features=list(features.unavailable),
        predicted_at=datetime.now(UTC),
        calibration_status=status,
        calibration_summary=summary,
        line_offset_from_recent_median=line_offset,
    )
