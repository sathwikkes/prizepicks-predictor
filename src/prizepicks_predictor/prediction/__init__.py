"""Statistical prediction engine (Phase 2).

Pipeline: history (services) -> features -> projection model -> distribution ->
More/Less/Push probabilities. Each stage is a pure function or small class so it
can be tested and swapped independently.
"""

from prizepicks_predictor.prediction.calibrators import CalibrationArtifact, CalibratorRegistry
from prizepicks_predictor.prediction.schemas import (
    DistributionFamily,
    PredictionRequest,
    PredictionResult,
    StatType,
    WarningCode,
)
from prizepicks_predictor.prediction.service import (
    InsufficientDataError,
    PredictionError,
    UnknownModelError,
    predict,
)

__all__ = [
    "CalibrationArtifact",
    "CalibratorRegistry",
    "DistributionFamily",
    "InsufficientDataError",
    "PredictionError",
    "PredictionRequest",
    "PredictionResult",
    "StatType",
    "UnknownModelError",
    "WarningCode",
    "predict",
]
