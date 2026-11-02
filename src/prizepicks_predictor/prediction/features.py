"""Feature engineering from historical game logs.

All functions are pure. The caller is responsible for passing only games that
occur strictly before the prediction cutoff; ``build_features`` additionally
asserts this so leakage cannot slip through silently.

Conventions
-----------
* "Games" means games with minutes > 0. Zero-minute rows (DNP, inactive) are
  excluded from every average and counted separately.
* Rolling windows (last 5/10, EWMA) use the most recent games regardless of
  season, because a player's recent form is informative across a season
  boundary; a warning flags when the window crosses one.
* Season averages use the season containing ``as_of_date``. If that season has
  no games yet, the previous season is used and a warning is raised.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import date

from prizepicks_predictor.prediction.schemas import FeatureSet, StatType, WarningCode
from prizepicks_predictor.schemas.game_log import GameLogRow, season_start_year_for

DEFAULT_EWMA_HALFLIFE = 5.0
MINUTES_CV_WARNING = 0.30
LONG_LAYOFF_DAYS = 14


def stat_value(row: GameLogRow, stat: StatType) -> int:
    return int(getattr(row, stat.value))


def mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def sample_std(values: Sequence[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def ewma(values_oldest_first: Sequence[float], halflife: float) -> float | None:
    """Exponentially weighted mean; the most recent value has weight 1.

    Weight for a value ``k`` games old is ``0.5 ** (k / halflife)``.
    """
    if not values_oldest_first:
        return None
    if halflife <= 0:
        raise ValueError("halflife must be positive")
    n = len(values_oldest_first)
    weights = [0.5 ** ((n - 1 - i) / halflife) for i in range(n)]
    weighted = sum(w * v for w, v in zip(weights, values_oldest_first, strict=True))
    return float(weighted / sum(weights))


def per_minute_rate(stat_total: float, minutes_total: float) -> float | None:
    return stat_total / minutes_total if minutes_total > 0 else None


def build_features(
    history_desc: Sequence[GameLogRow],
    *,
    stat: StatType,
    as_of_date: date,
    ewma_halflife: float = DEFAULT_EWMA_HALFLIFE,
) -> FeatureSet:
    """Compute features for ``stat`` from ``history_desc`` (most recent first)."""
    for row in history_desc:
        if row.game_date >= as_of_date:
            raise ValueError(f"leakage: game on {row.game_date} is not before cutoff {as_of_date}")

    warnings: list[WarningCode] = [WarningCode.MATCHUP_CONTEXT_UNAVAILABLE]

    played = [r for r in history_desc if r.minutes > 0]
    dnp = len(history_desc) - len(played)
    if dnp:
        warnings.append(WarningCode.DNP_GAMES_EXCLUDED)

    values_desc = [float(stat_value(r, stat)) for r in played]
    minutes_desc = [r.minutes for r in played]

    # --- season -----------------------------------------------------------
    target_season = season_start_year_for(as_of_date)
    season_rows = [r for r in played if season_start_year_for(r.game_date) == target_season]
    season_used: int | None = target_season
    if not season_rows and played:
        prev = max(season_start_year_for(r.game_date) for r in played)
        season_rows = [r for r in played if season_start_year_for(r.game_date) == prev]
        season_used = prev
        warnings.append(WarningCode.SEASON_FALLBACK)
    elif not played:
        season_used = None
    season_vals = [float(stat_value(r, stat)) for r in season_rows]
    season_mins = [r.minutes for r in season_rows]

    # --- rolling windows --------------------------------------------------
    last5, last10 = played[:5], played[:10]
    last5_vals = values_desc[:5]
    last10_vals = values_desc[:10]
    if last10 and len({season_start_year_for(r.game_date) for r in last10}) > 1:
        warnings.append(WarningCode.WINDOW_CROSSES_SEASON)
    teams_last10 = sorted({r.team for r in last10})
    if len(teams_last10) > 1:
        warnings.append(WarningCode.TEAM_CHANGE_IN_WINDOW)

    # --- minutes ----------------------------------------------------------
    minutes_recent = mean(minutes_desc[:5])
    minutes_std = sample_std(minutes_desc[:10])
    season_min_mean = mean(season_mins)
    if (
        minutes_recent
        and minutes_std is not None
        and minutes_std / minutes_recent > MINUTES_CV_WARNING
    ):
        warnings.append(WarningCode.MINUTES_VOLATILE)

    # --- rest -------------------------------------------------------------
    last_date = history_desc[0].game_date if history_desc else None
    rest_days = (as_of_date - last_date).days if last_date else None
    if rest_days is not None and rest_days > LONG_LAYOFF_DAYS:
        warnings.append(WarningCode.LONG_LAYOFF)

    # --- splits -----------------------------------------------------------
    home_vals = [float(stat_value(r, stat)) for r in played if r.is_home]
    away_vals = [float(stat_value(r, stat)) for r in played if not r.is_home]

    return FeatureSet(
        stat_type=stat,
        as_of_date=as_of_date,
        cutoff_last_game_date=last_date,
        games_total=len(played),
        dnp_games=dnp,
        season_start_year=season_used,
        season_games=len(season_rows),
        season_mean=mean(season_vals),
        season_std=sample_std(season_vals),
        season_minutes_mean=season_min_mean,
        last5_mean=mean(last5_vals),
        last5_games=len(last5),
        last10_mean=mean(last10_vals),
        last10_games=len(last10),
        rolling10_std=sample_std(last10_vals),
        ewma_mean=ewma(list(reversed(values_desc)), ewma_halflife),
        ewma_halflife_games=ewma_halflife,
        per_minute_season=per_minute_rate(sum(season_vals), sum(season_mins)),
        per_minute_recent=per_minute_rate(sum(last10_vals), sum(minutes_desc[:10])),
        minutes_recent_mean=minutes_recent,
        minutes_std=minutes_std,
        home_mean=mean(home_vals),
        home_games=len(home_vals),
        away_mean=mean(away_vals),
        away_games=len(away_vals),
        rest_days=rest_days,
        back_to_back=(rest_days == 1) if rest_days is not None else None,
        latest_team=played[0].team if played else None,
        teams_in_last10=teams_last10,
        warnings=warnings,
    )


def is_finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)
