"""Pydantic contracts for the prediction engine."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StatType(StrEnum):
    POINTS = "points"
    REBOUNDS = "rebounds"
    ASSISTS = "assists"


class DistributionFamily(StrEnum):
    AUTO = "auto"
    POISSON = "poisson"
    NEGATIVE_BINOMIAL = "negative_binomial"
    NORMAL = "normal"
    EMPIRICAL = "empirical"


class WarningCode(StrEnum):
    SMALL_SAMPLE = "small_sample"
    SEASON_FALLBACK = "season_fallback"
    WINDOW_CROSSES_SEASON = "window_crosses_season"
    TEAM_CHANGE_IN_WINDOW = "team_change_in_window"
    DNP_GAMES_EXCLUDED = "dnp_games_excluded"
    MINUTES_VOLATILE = "minutes_volatile"
    EMPIRICAL_FALLBACK = "empirical_fallback"
    LONG_LAYOFF = "long_layoff"
    MATCHUP_CONTEXT_UNAVAILABLE = "matchup_context_unavailable"


#: Matchup/availability inputs the engine is designed for but cannot compute yet.
UNAVAILABLE_FEATURES: tuple[str, ...] = (
    "opponent_defensive_rating",
    "expected_pace",
    "injury_report",
    "starting_lineup_status",
    "usage_rate",
    "teammate_availability",
)

MAX_LINE = {StatType.POINTS: 100.0, StatType.REBOUNDS: 40.0, StatType.ASSISTS: 30.0}


class PredictionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    player_id: int = Field(gt=0)
    stat_type: StatType
    line: float = Field(gt=0, description="Offered prop line, in 0.5 increments")
    as_of_date: date = Field(description="Target game date; only games strictly before count")
    model_name: str = "ewma"
    distribution: DistributionFamily = DistributionFamily.AUTO
    opponent: str | None = Field(default=None, min_length=2, max_length=5)
    is_home: bool | None = None

    @field_validator("line")
    @classmethod
    def _half_point_increment(cls, value: float) -> float:
        if not math.isclose(value * 2, round(value * 2), abs_tol=1e-9):
            raise ValueError("line must be a multiple of 0.5")
        return value

    @model_validator(mode="after")
    def _line_in_range(self) -> PredictionRequest:
        if self.line > MAX_LINE[self.stat_type]:
            raise ValueError(f"line {self.line} exceeds maximum for {self.stat_type.value}")
        return self

    @property
    def line_is_integer(self) -> bool:
        return math.isclose(self.line, round(self.line), abs_tol=1e-9)


class FeatureSet(BaseModel):
    """Historical features computed strictly from games before the cutoff.

    ``None`` means the feature could not be computed from the available data; it
    is never imputed.
    """

    model_config = ConfigDict(frozen=True)

    stat_type: StatType
    as_of_date: date
    cutoff_last_game_date: date | None

    games_total: int = Field(description="Games with minutes > 0 before the cutoff")
    dnp_games: int = Field(description="Zero-minute games excluded from all averages")

    season_start_year: int | None
    season_games: int
    season_mean: float | None
    season_std: float | None
    season_minutes_mean: float | None

    last5_mean: float | None
    last5_games: int
    last10_mean: float | None
    last10_games: int
    rolling10_std: float | None

    ewma_mean: float | None
    ewma_halflife_games: float

    per_minute_season: float | None
    per_minute_recent: float | None
    minutes_recent_mean: float | None
    minutes_std: float | None

    home_mean: float | None
    home_games: int
    away_mean: float | None
    away_games: int

    rest_days: int | None = Field(description="Days between last game and as_of_date")
    back_to_back: bool | None

    latest_team: str | None
    teams_in_last10: list[str]

    warnings: list[WarningCode]
    unavailable: list[str] = Field(default_factory=lambda: list(UNAVAILABLE_FEATURES))


class ModelOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    projection: float = Field(ge=0)
    projected_minutes: float | None = None
    assumptions: list[str]


class DistributionSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    family: DistributionFamily
    mean: float
    std: float
    parameters: dict[str, float]
    variance_sample_size: int
    notes: list[str]


class OutcomeProbabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    more: float = Field(ge=0, le=1)
    less: float = Field(ge=0, le=1)
    push: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sums_to_one(self) -> OutcomeProbabilities:
        total = self.more + self.less + self.push
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"probabilities sum to {total}, expected 1")
        return self


class PredictionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    player_id: int
    player_name: str
    team: str | None
    stat_type: StatType
    line: float
    as_of_date: date

    model: ModelOutput
    distribution: DistributionSummary
    probabilities: OutcomeProbabilities
    interval_80: tuple[float, float]

    sample_size: int
    features: FeatureSet
    warnings: list[WarningCode]
    unavailable_features: list[str]
    predicted_at: datetime

    #: Probabilities are model outputs; calibration is unverified until Phase 3.
    calibration_status: str = "unvalidated"
