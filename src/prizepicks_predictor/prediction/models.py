"""Baseline projection models.

Each model maps a ``FeatureSet`` to a point projection plus a list of the
assumptions it relied on. Models are deliberately simple and interpretable;
they are the yardstick any later ML model must beat in Phase 3.

Adding a model: implement ``ProjectionModel`` and register it in ``MODELS``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from prizepicks_predictor.prediction.schemas import FeatureSet, ModelOutput

SCHEDULE_FACTORS_PATH = Path(__file__).resolve().parents[3] / "artifacts" / "schedule_factors.json"
NEUTRAL_FACTORS: dict[str, float] = {
    "back_to_back": 1.0,
    "three_in_four": 1.0,
    "long_rest": 1.0,
    "normal": 1.0,
    "home": 1.0,
    "away": 1.0,
}


@lru_cache(maxsize=1)
def load_schedule_factors(path: Path = SCHEDULE_FACTORS_PATH) -> dict[str, dict[str, float]]:
    """{stat: {bucket: multiplicative factor}} from the committed artifact; neutral if absent."""
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        stat: {k: float(v) for k, v in facs.items()} for stat, facs in payload["factors"].items()
    }


class ModelInputError(ValueError):
    """The feature set lacks what this model needs."""


class ProjectionModel(Protocol):
    name: str
    version: str
    min_games: int

    def project(self, features: FeatureSet) -> ModelOutput: ...


def _require(value: float | None, what: str) -> float:
    if value is None:
        raise ModelInputError(f"{what} unavailable")
    return value


class SeasonAverageModel:
    """Model A: mean of the current season (previous season if none yet)."""

    name = "season_average"
    version = "1.0"
    min_games = 3

    def project(self, features: FeatureSet) -> ModelOutput:
        mean = _require(features.season_mean, "season mean")
        assumptions = [
            f"projection = mean of {features.season_games} games in season "
            f"{features.season_start_year}",
            "every game in the season is weighted equally",
            "no adjustment for opponent, minutes, or rest",
        ]
        return ModelOutput(
            name=self.name, version=self.version, projection=mean, assumptions=assumptions
        )


class RollingAverageModel:
    """Model B: mean of the last N games (default 10)."""

    version = "1.0"
    min_games = 3

    def __init__(self, window: int = 10) -> None:
        if window not in (5, 10):
            raise ValueError("window must be 5 or 10")
        self.window = window
        self.name = f"rolling_{window}"

    def project(self, features: FeatureSet) -> ModelOutput:
        if self.window == 5:
            mean, games = _require(features.last5_mean, "last-5 mean"), features.last5_games
        else:
            mean, games = _require(features.last10_mean, "last-10 mean"), features.last10_games
        assumptions = [
            f"projection = mean of the last {games} games (window {self.window})",
            "recent games may span a season boundary or a team change",
            "no adjustment for opponent, minutes, or rest",
        ]
        return ModelOutput(
            name=self.name, version=self.version, projection=mean, assumptions=assumptions
        )


class EwmaModel:
    """Model C: exponentially weighted mean over all available history."""

    name = "ewma"
    version = "1.0"
    min_games = 3

    def project(self, features: FeatureSet) -> ModelOutput:
        mean = _require(features.ewma_mean, "EWMA mean")
        assumptions = [
            f"projection = EWMA over {features.games_total} games, "
            f"half-life {features.ewma_halflife_games:g} games",
            "older games retain geometrically decaying weight",
            "no adjustment for opponent, minutes, or rest",
        ]
        return ModelOutput(
            name=self.name, version=self.version, projection=mean, assumptions=assumptions
        )


class MinutesAdjustedModel:
    """Model D: recent per-minute rate times projected minutes.

    Projected minutes blend recent (last 5) and season minutes 60/40. The split
    is a prior, not a fitted value; Phase 3 should tune or replace it.
    """

    name = "minutes_adjusted"
    version = "1.0"
    min_games = 3
    recent_weight = 0.6

    def project(self, features: FeatureSet) -> ModelOutput:
        rate = _require(features.per_minute_recent, "recent per-minute rate")
        recent_min = _require(features.minutes_recent_mean, "recent minutes")
        season_min = features.season_minutes_mean
        if season_min is None:
            projected_minutes = recent_min
            blend_note = "projected minutes = last-5 mean (no season minutes available)"
        else:
            projected_minutes = (
                self.recent_weight * recent_min + (1 - self.recent_weight) * season_min
            )
            blend_note = (
                f"projected minutes = {self.recent_weight:.0%} last-5 mean "
                f"({recent_min:.1f}) + {1 - self.recent_weight:.0%} season mean ({season_min:.1f})"
            )
        assumptions = [
            f"per-minute rate from last {features.last10_games} games = {rate:.4f}",
            blend_note,
            "production scales linearly with minutes",
            "no adjustment for opponent or rest",
        ]
        return ModelOutput(
            name=self.name,
            version=self.version,
            projection=rate * projected_minutes,
            projected_minutes=projected_minutes,
            assumptions=assumptions,
        )


class ScheduleAdjustedEwmaModel:
    """EWMA projection multiplied by transparent rest and venue factors.

    projection = ewma * rest_factor[rest_bucket] * venue_factor[home|away]

    Factors are per-stat ratios of actual to EWMA projection within each schedule bucket,
    normalised to the "normal rest" bucket, fitted on the development season and stored in
    ``artifacts/schedule_factors.json``. Without that file, or without schedule context, the
    model reduces exactly to EWMA and says so.
    """

    name = "ewma_schedule"
    version = "1.0"
    min_games = 3

    def __init__(self, factors: dict[str, dict[str, float]] | None = None) -> None:
        self._factors = factors

    def factors_for(self, stat: str) -> dict[str, float]:
        table = self._factors if self._factors is not None else load_schedule_factors()
        return {**NEUTRAL_FACTORS, **table.get(stat, {})}

    def project(self, features: FeatureSet) -> ModelOutput:
        base = _require(features.ewma_mean, "EWMA mean")
        facs = self.factors_for(features.stat_type.value)
        ctx = features.schedule
        assumptions = [
            f"base = EWMA over {features.games_total} games, half-life "
            f"{features.ewma_halflife_games:g} games ({base:.2f})",
        ]
        rest_factor = venue_factor = 1.0
        if ctx is None or ctx.rest_days is None:
            assumptions.append("no schedule context available: no rest adjustment applied")
        else:
            rest_factor = facs.get(ctx.rest_bucket, 1.0)
            assumptions.append(
                f"rest bucket '{ctx.rest_bucket}' (rest {ctx.rest_days}d, "
                f"3-in-4={bool(ctx.three_in_four)}): factor {rest_factor:.4f}"
            )
        if ctx is None or ctx.is_home is None:
            assumptions.append("venue unknown: no home/away adjustment applied")
        else:
            venue_factor = facs["home" if ctx.is_home else "away"]
            assumptions.append(
                f"venue {'home' if ctx.is_home else 'away'}: factor {venue_factor:.4f}"
            )
        if self._factors is None and not load_schedule_factors():
            assumptions.append("schedule_factors.json not found: all factors neutral (1.0)")
        assumptions.append("factors are multiplicative ratios fitted on the development season")
        return ModelOutput(
            name=self.name,
            version=self.version,
            projection=base * rest_factor * venue_factor,
            assumptions=assumptions,
        )


OPPONENT_FACTORS_PATH = Path(__file__).resolve().parents[3] / "artifacts" / "opponent_factors.json"


@lru_cache(maxsize=1)
def load_opponent_factors(path: Path = OPPONENT_FACTORS_PATH) -> dict[str, dict[str, float]]:
    """{stat: {"pace": a, "defense": b}} elasticities; empty (neutral) if absent."""
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        stat: {k: float(v) for k, v in facs.items()} for stat, facs in payload["factors"].items()
    }


class OpponentAdjustedEwmaModel:
    """EWMA projection scaled by opponent pace and defence relative to the league.

    projection = ewma * opp_pace_rel ** a_stat * opp_def_rel ** b_stat

    ``opp_pace_rel`` and ``opp_def_rel`` are shrunk ratios to the league average (1.0 = average;
    def_rel > 1 = weaker defence). ``a`` and ``b`` are per-stat elasticities fitted by least
    squares on the development season (``ppp fit-opponent-factors``) and stored in
    ``artifacts/opponent_factors.json``. Without the file or without context the model
    reduces exactly to EWMA and says so.
    """

    name = "ewma_opponent"
    version = "1.0"
    min_games = 3

    def __init__(self, factors: dict[str, dict[str, float]] | None = None) -> None:
        self._factors = factors

    def factors_for(self, stat: str) -> dict[str, float]:
        table = self._factors if self._factors is not None else load_opponent_factors()
        return {"pace": 0.0, "defense": 0.0, **table.get(stat, {})}

    def project(self, features: FeatureSet) -> ModelOutput:
        base = _require(features.ewma_mean, "EWMA mean")
        facs = self.factors_for(features.stat_type.value)
        ctx = features.opponent
        assumptions = [
            f"base = EWMA over {features.games_total} games, half-life "
            f"{features.ewma_halflife_games:g} games ({base:.2f})",
        ]
        factor = 1.0
        if ctx is None or not ctx.available:
            assumptions.append("no opponent context available: no adjustment applied")
        else:
            factor = (ctx.opp_pace_rel ** facs["pace"]) * (ctx.opp_def_rel ** facs["defense"])
            assumptions.append(
                f"opponent {ctx.opponent}: pace rel {ctx.opp_pace_rel:.3f}^{facs['pace']:.3f} x "
                f"defence rel {ctx.opp_def_rel:.3f}^{facs['defense']:.3f} = {factor:.4f} "
                f"({ctx.games_used} opponent games, shrunk toward league average)"
            )
        if self._factors is None and not load_opponent_factors():
            assumptions.append("opponent_factors.json not found: elasticities 0 (neutral)")
        assumptions.append("elasticities fitted on the development season; research model")
        return ModelOutput(
            name=self.name, version=self.version, projection=base * factor, assumptions=assumptions
        )


MODELS: dict[str, ProjectionModel] = {
    "season_average": SeasonAverageModel(),
    "rolling_5": RollingAverageModel(5),
    "rolling_10": RollingAverageModel(10),
    "ewma": EwmaModel(),
    "ewma_schedule": ScheduleAdjustedEwmaModel(),
    "ewma_opponent": OpponentAdjustedEwmaModel(),
    "minutes_adjusted": MinutesAdjustedModel(),
}

DEFAULT_MODEL = "ewma"


def get_model(name: str) -> ProjectionModel:
    try:
        return MODELS[name]
    except KeyError as exc:
        raise KeyError(f"unknown model {name!r}; available: {', '.join(sorted(MODELS))}") from exc
