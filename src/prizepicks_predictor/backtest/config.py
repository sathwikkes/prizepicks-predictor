"""Backtest configuration: everything needed to reproduce a run."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from prizepicks_predictor.prediction.models import MODELS
from prizepicks_predictor.prediction.schemas import DistributionFamily, StatType
from prizepicks_predictor.prediction.service import VARIANCE_WINDOW

_SEASON_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")


def parse_season(label: str | int) -> int:
    """'2024-25' or '2024' -> 2024 (season start year)."""
    text = str(label).strip()
    match = _SEASON_RE.match(text)
    if not match:
        raise ValueError(f"season must look like 2024-25 or 2024, got {label!r}")
    start = int(match.group(1))
    if match.group(2) is not None and int(match.group(2)) != (start + 1) % 100:
        raise ValueError(f"season {label!r} is not consecutive years")
    return start


def season_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    seasons: list[int] = Field(
        min_length=1, description="Season start years whose games are targets"
    )
    stats: list[StatType] = Field(default_factory=lambda: list(StatType))
    models: list[str] = Field(default_factory=lambda: sorted(MODELS))
    families: list[DistributionFamily] = Field(
        default_factory=lambda: [DistributionFamily.AUTO, DistributionFamily.NORMAL]
    )
    include_empirical: bool = True

    min_prior_games: int = Field(default=10, ge=1)
    line_window: int = Field(default=10, ge=3, description="Games in the trailing median for lines")
    line_offsets: list[int] = Field(default_factory=lambda: [-2, -1, 0, 1, 2])
    variance_window: int = VARIANCE_WINDOW
    interval: tuple[float, float] = (0.10, 0.90)
    season_types: tuple[str, ...] = ("regular",)

    max_observations: int | None = Field(default=None, ge=1)
    seed: int = 0
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("models")
    @classmethod
    def _known_models(cls, names: list[str]) -> list[str]:
        unknown = sorted(set(names) - set(MODELS))
        if unknown:
            raise ValueError(f"unknown models {unknown}; available: {sorted(MODELS)}")
        if not names:
            raise ValueError("at least one model is required")
        return list(dict.fromkeys(names))

    @field_validator("families")
    @classmethod
    def _no_empirical_in_families(cls, fams: list[DistributionFamily]) -> list[DistributionFamily]:
        if DistributionFamily.EMPIRICAL in fams:
            raise ValueError("use include_empirical for the model-independent empirical reference")
        if not fams:
            raise ValueError("at least one distribution family is required")
        return list(dict.fromkeys(fams))

    @property
    def season_labels(self) -> list[str]:
        return [season_label(y) for y in self.seasons]
