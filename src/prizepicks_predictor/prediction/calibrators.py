"""Versioned probability-calibration artifacts and their application to predictions.

An artifact is a JSON file holding a fitted isotonic (or Platt) map from the model's raw
P(more | no push) to an observed frequency, plus everything needed to know *what* it was
fitted on and whether it was validated out of sample. Artifacts are matched to a
prediction only when stat, model name, model version and requested distribution family
all agree; otherwise the prediction stays uncalibrated and says why.

Push handling: calibrators are fitted on half-point synthetic lines, where no push is
possible, so the quantity they map is the conditional probability of More among non-push
outcomes. At integer lines the modelled push probability is kept as is and the remaining
mass is split by the calibrated conditional probability. More + Less + Push still sum to 1.

What calibration does and does not mean here: it was fitted and validated against
*synthetic* evaluation thresholds derived from each player's own history. It says nothing
about calibration against real PrizePicks or sportsbook lines.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from prizepicks_predictor.prediction.schemas import (
    DistributionFamily,
    OutcomeProbabilities,
    StatType,
)

ARTIFACT_SCHEMA_VERSION = 1
LINE_POLICY = (
    "synthetic half-point lines (floor(trailing-10 median)+0.5, offsets -2..+2), pooled; "
    "maps P(more | no push); not validated against market lines"
)


class HeldOutSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: str
    seasons: list[int]
    n_eval: int
    brier_uncalibrated: float
    brier_calibrated: float
    log_loss_uncalibrated: float
    log_loss_calibrated: float
    ece_uncalibrated: float
    ece_calibrated: float


class CalibrationArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = ARTIFACT_SCHEMA_VERSION
    artifact_id: str
    stat: StatType
    model_name: str
    model_version: str
    family: DistributionFamily
    method: Literal["isotonic", "platt"]
    # isotonic: block right edges and values; platt: a, b
    x: list[float] = Field(default_factory=list)
    y: list[float] = Field(default_factory=list)
    a: float | None = None
    b: float | None = None
    n_fit: int = Field(ge=1)
    fit_seasons: list[int]
    fit_runs: list[str]
    data_source: str
    data_latest_game_date: date | None
    fitted_at: datetime
    validation_status: Literal["held_out_validated", "in_sample_only"]
    held_out: HeldOutSummary | None = None
    line_policy: str = LINE_POLICY
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> CalibrationArtifact:
        if self.schema_version != ARTIFACT_SCHEMA_VERSION:
            raise ValueError(f"unsupported artifact schema_version {self.schema_version}")
        if self.method == "isotonic":
            if not self.x or len(self.x) != len(self.y):
                raise ValueError("isotonic artifact needs equal-length x and y")
            if any(b < a for a, b in zip(self.y, self.y[1:], strict=False)):
                raise ValueError("isotonic y must be non-decreasing")
            if min(self.y) < 0 or max(self.y) > 1:
                raise ValueError("isotonic y must lie in [0, 1]")
        elif self.a is None or self.b is None:
            raise ValueError("platt artifact needs a and b")
        return self

    # --- application --------------------------------------------------------
    def calibrate_conditional(self, p_more_given_no_push: float) -> float:
        p = min(max(p_more_given_no_push, 0.0), 1.0)
        if self.method == "isotonic":
            idx = int(
                np.clip(np.searchsorted(np.asarray(self.x), p, side="left"), 0, len(self.y) - 1)
            )
            return float(self.y[idx])
        assert self.a is not None and self.b is not None
        eps = 1e-6
        p = min(max(p, eps), 1 - eps)
        z = self.a * float(np.log(p / (1 - p))) + self.b
        return float(1 / (1 + np.exp(-z)))

    def apply(self, raw: OutcomeProbabilities) -> OutcomeProbabilities:
        non_push = raw.more + raw.less
        if non_push <= 1e-12:
            return raw
        cond = self.calibrate_conditional(raw.more / non_push)
        return OutcomeProbabilities(more=cond * non_push, less=(1 - cond) * non_push, push=raw.push)

    def applies_to(
        self, *, stat: StatType, model_name: str, model_version: str, family: DistributionFamily
    ) -> bool:
        return (
            self.stat is stat
            and self.model_name == model_name
            and self.model_version == model_version
            and self.family is family
        )

    # --- persistence --------------------------------------------------------
    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.stat.value}_{self.model_name}_{self.family.value}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> CalibrationArtifact:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read calibration artifact {path}: {exc}") from exc
        return cls.model_validate(payload)


def make_artifact_id(
    *,
    method: str,
    stat: StatType,
    model_name: str,
    model_version: str,
    family: DistributionFamily,
    fit_seasons: list[int],
    x: list[float],
    y: list[float],
    a: float | None,
    b: float | None,
) -> str:
    digest = hashlib.sha256(json.dumps([x, y, a, b]).encode()).hexdigest()[:8]
    seasons = "+".join(f"{s}-{(s + 1) % 100:02d}" for s in fit_seasons)
    return (
        f"{method}.{stat.value}.{model_name}-{model_version}.{family.value}.fit{seasons}.{digest}"
    )


class CalibratorRegistry:
    """All artifacts in a directory, looked up by (stat, model, version, family)."""

    def __init__(
        self, artifacts: list[CalibrationArtifact], *, directory: Path | None = None
    ) -> None:
        self.artifacts = artifacts
        self.directory = directory

    @classmethod
    def load(cls, directory: Path) -> CalibratorRegistry:
        artifacts: list[CalibrationArtifact] = []
        if directory.is_dir():
            for path in sorted(directory.glob("*.json")):
                artifacts.append(CalibrationArtifact.load(path))
        return cls(artifacts, directory=directory)

    @classmethod
    def empty(cls) -> CalibratorRegistry:
        return cls([])

    def find(
        self, *, stat: StatType, model_name: str, model_version: str, family: DistributionFamily
    ) -> CalibrationArtifact | None:
        for art in self.artifacts:
            if art.applies_to(
                stat=stat, model_name=model_name, model_version=model_version, family=family
            ):
                return art
        return None

    def __len__(self) -> int:
        return len(self.artifacts)


def utc_now() -> datetime:
    return datetime.now(UTC)
