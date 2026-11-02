"""Walk-forward engine.

For every eligible (player, target game, stat) the engine rebuilds the prediction the
live service would have produced using only games strictly before the target date,
scores it against the realised stat, and records everything. Model, family and line
computations share one feature set per observation, so the comparison is on identical
inputs.

Eligibility policy (documented here because it defines the cohort):
* target game is in a requested season and season type (regular season by default);
* the player has at least ``min_prior_games`` prior games with minutes > 0, counting
  earlier seasons (regular season only), so new players and early-season games without
  enough history are excluded and counted;
* the target game has minutes > 0 (a DNP would void a prop, and DNP rows are not stored
  by default anyway); zero-minute targets are excluded and counted;
* prior history includes all earlier games regardless of team (trades keep identity) and
  may span a season boundary (the feature set flags it).
"""

from __future__ import annotations

import math
import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.config import BacktestConfig
from prizepicks_predictor.backtest.history import load_history
from prizepicks_predictor.backtest.lines import synthetic_lines
from prizepicks_predictor.prediction.distributions import (
    MIN_EMPIRICAL_SAMPLE,
    empirical_pmf,
    pmf_array,
    predictive_variance,
    resolve_family,
)
from prizepicks_predictor.prediction.features import build_features, stat_value
from prizepicks_predictor.prediction.models import ModelInputError, get_model
from prizepicks_predictor.prediction.schemas import DistributionFamily, StatType
from prizepicks_predictor.schemas.game_log import GameLogRow, season_start_year_for

EMPIRICAL_MODEL_NAME = "empirical_history"
NLL_CAP = 30.0


@dataclass(frozen=True)
class Observation:
    obs_id: int
    season: int
    stat: StatType
    player_id: int
    player_name: str
    game_date: str
    team: str
    opponent: str
    is_home: bool
    actual: int
    actual_minutes: float
    prior_games: int
    season_games: int
    minutes_recent: float | None
    prior_start_rate: float | None
    trailing_median_line: float
    lines: tuple[float, ...]


@dataclass(frozen=True)
class PredictionRecord:
    obs_id: int
    model: str
    requested_family: str
    family: str
    projection: float
    mean: float
    std: float
    q_lo: float
    q_hi: float
    nll: float
    covered: bool
    p_more: tuple[float, ...]  # one per line


@dataclass
class BacktestResult:
    config: BacktestConfig
    observations: list[Observation]
    predictions: list[PredictionRecord]
    exclusions: Counter[str]
    missing: Counter[str]  # "model/family" -> count of observations without a prediction
    candidates: int
    started_at: datetime
    elapsed_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def _kmax(mean: float, variance: float) -> int:
    return int(min(300, max(40, mean + 12 * math.sqrt(variance) + 10)))


def _score(
    pmf: np.ndarray, *, actual: int, lines: tuple[float, ...], interval: tuple[float, float]
) -> tuple[float, float, float, float, bool, tuple[float, ...]]:
    """Returns (nll, q_lo, q_hi, mean, covered, p_more per line) from a pmf array."""
    total = float(pmf.sum())
    cdf = np.cumsum(pmf)
    p_actual = float(pmf[actual]) if actual < len(pmf) else 0.0
    nll = min(NLL_CAP, -math.log(max(p_actual, 1e-300)))
    q_lo = float(np.searchsorted(cdf, interval[0] * total))
    q_hi = float(np.searchsorted(cdf, interval[1] * total))
    p_more = tuple(
        float(max(0.0, 1.0 - cdf[math.floor(line)])) if math.floor(line) < len(cdf) else 0.0
        for line in lines
    )
    mean = float(np.dot(np.arange(len(pmf)), pmf))
    return nll, q_lo, q_hi, mean, q_lo <= actual <= q_hi, p_more


def enumerate_candidates(
    history: dict[int, list[GameLogRow]], config: BacktestConfig
) -> tuple[list[tuple[int, int]], Counter[str]]:
    """All (player_id, index) target games meeting the eligibility policy, plus exclusions."""
    wanted = set(config.seasons)
    exclusions: Counter[str] = Counter()
    candidates: list[tuple[int, int]] = []
    for pid, games in history.items():
        played_before = 0
        for idx, g in enumerate(games):
            in_scope = season_start_year_for(g.game_date) in wanted
            if in_scope and config.date_from and g.game_date < config.date_from:
                in_scope = False
            if in_scope and config.date_to and g.game_date > config.date_to:
                in_scope = False
            if in_scope:
                if g.minutes <= 0:
                    exclusions["target_zero_minutes"] += 1
                elif played_before < config.min_prior_games:
                    exclusions["insufficient_prior_games"] += 1
                else:
                    candidates.append((pid, idx))
            if g.minutes > 0:
                played_before += 1
    return candidates, exclusions


def run_backtest(
    session: Session,
    config: BacktestConfig,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> BacktestResult:
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    history, names = load_history(session, season_types=config.season_types)
    candidates, exclusions = enumerate_candidates(history, config)
    total_candidates = len(candidates)

    if config.max_observations is not None and len(candidates) > config.max_observations:
        rng = random.Random(config.seed)
        candidates = sorted(rng.sample(candidates, config.max_observations))
        exclusions["sampled_out_by_max_observations"] += total_candidates - len(candidates)

    models = [get_model(name) for name in config.models]
    observations: list[Observation] = []
    predictions: list[PredictionRecord] = []
    missing: Counter[str] = Counter()
    obs_id = 0

    for n_done, (pid, idx) in enumerate(candidates, start=1):
        games = history[pid]
        target = games[idx]
        prior_desc = games[:idx][::-1]
        # Belt and braces: the slice is strictly before the target by construction.
        assert all(r.game_date < target.game_date for r in prior_desc)
        played_desc = [r for r in prior_desc if r.minutes > 0]
        starts = [r.started for r in played_desc[:10] if r.started is not None]

        for stat in config.stats:
            features = build_features(prior_desc, stat=stat, as_of_date=target.game_date)
            values_desc = [float(stat_value(r, stat)) for r in played_desc]
            recent = values_desc[: config.variance_window]
            actual = stat_value(target, stat)
            lines = tuple(
                synthetic_lines(values_desc, window=config.line_window, offsets=config.line_offsets)
            )
            obs_id += 1
            observations.append(
                Observation(
                    obs_id=obs_id,
                    season=season_start_year_for(target.game_date),
                    stat=stat,
                    player_id=pid,
                    player_name=names[pid],
                    game_date=target.game_date.isoformat(),
                    team=target.team,
                    opponent=target.opponent,
                    is_home=target.is_home,
                    actual=actual,
                    actual_minutes=target.minutes,
                    prior_games=features.games_total,
                    season_games=features.season_games,
                    minutes_recent=features.minutes_recent_mean,
                    prior_start_rate=(sum(starts) / len(starts)) if starts else None,
                    trailing_median_line=lines[config.line_offsets.index(0)]
                    if 0 in config.line_offsets
                    else lines[0],
                    lines=lines,
                )
            )

            for model in models:
                try:
                    output = model.project(features)
                except ModelInputError:
                    for fam in config.families:
                        missing[f"{model.name}/{fam.value}"] += 1
                    continue
                variance = predictive_variance(recent, mean=output.projection).variance
                for fam in config.families:
                    resolved = resolve_family(fam, output.projection, variance)
                    pmf = pmf_array(
                        resolved,
                        mean=output.projection,
                        variance=variance,
                        kmax=_kmax(output.projection, variance),
                    )
                    nll, q_lo, q_hi, mean, covered, p_more = _score(
                        pmf, actual=actual, lines=lines, interval=config.interval
                    )
                    predictions.append(
                        PredictionRecord(
                            obs_id=obs_id,
                            model=model.name,
                            requested_family=fam.value,
                            family=resolved.value,
                            projection=output.projection,
                            mean=mean,
                            std=math.sqrt(variance),
                            q_lo=q_lo,
                            q_hi=q_hi,
                            nll=nll,
                            covered=covered,
                            p_more=p_more,
                        )
                    )

            if config.include_empirical:
                key = f"{EMPIRICAL_MODEL_NAME}/{DistributionFamily.EMPIRICAL.value}"
                if len(recent) < MIN_EMPIRICAL_SAMPLE:
                    missing[key] += 1
                else:
                    table = empirical_pmf(recent)
                    kmax = max(table) + 1
                    pmf = np.zeros(kmax + 1)
                    for k, p in table.items():
                        pmf[k] = p
                    emp_mean = sum(recent) / len(recent)
                    nll, q_lo, q_hi, mean, covered, p_more = _score(
                        pmf, actual=actual, lines=lines, interval=config.interval
                    )
                    predictions.append(
                        PredictionRecord(
                            obs_id=obs_id,
                            model=EMPIRICAL_MODEL_NAME,
                            requested_family=DistributionFamily.EMPIRICAL.value,
                            family=DistributionFamily.EMPIRICAL.value,
                            projection=emp_mean,
                            mean=mean,
                            std=float(np.std(recent, ddof=1)),
                            q_lo=q_lo,
                            q_hi=q_hi,
                            nll=nll,
                            covered=covered,
                            p_more=p_more,
                        )
                    )
        if progress and (n_done % 500 == 0 or n_done == len(candidates)):
            progress(n_done, len(candidates))

    return BacktestResult(
        config=config,
        observations=observations,
        predictions=predictions,
        exclusions=exclusions,
        missing=missing,
        candidates=total_candidates,
        started_at=started,
        elapsed_seconds=time.perf_counter() - t0,
    )
