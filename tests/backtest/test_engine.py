from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.config import BacktestConfig
from prizepicks_predictor.backtest.engine import (
    EMPIRICAL_MODEL_NAME,
    enumerate_candidates,
    run_backtest,
)
from prizepicks_predictor.backtest.history import load_history
from prizepicks_predictor.backtest.report import analyse, build_report, read_run, write_run
from prizepicks_predictor.db import create_db_engine, get_session_factory, init_db
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction import PredictionRequest, StatType, predict
from prizepicks_predictor.prediction.distributions import (
    DistributionFamily,
    Predictive,
    build_predictive,
    pmf_array,
    resolve_family,
)
from prizepicks_predictor.prediction.models import MODELS
from prizepicks_predictor.services import find_players
from tests.conftest import BASE_HEADER, write_csv

HISTORY = [
    f"2024-11-{d:02d},P1,Walk Forward,BOS,LAL,home,30:00,{pts},5,4"
    for d, pts in zip(
        range(1, 25, 2), [20, 22, 18, 26, 21, 24, 19, 28, 23, 25, 27, 22], strict=True
    )
]


def _db(tmp_path: Path, name: str, rows: list[str]) -> Session:
    engine = create_db_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
    init_db(engine)
    s = get_session_factory(engine)()
    load_game_logs(s, CsvGameLogProvider(write_csv(tmp_path / f"{name}.csv", BASE_HEADER, *rows)))
    return s


# --- fast pmf path must match the live engine ---------------------------------------
@pytest.mark.parametrize(
    "family",
    [DistributionFamily.POISSON, DistributionFamily.NEGATIVE_BINOMIAL, DistributionFamily.NORMAL],
)
def test_pmf_array_matches_predictive(family: DistributionFamily) -> None:
    pred = Predictive(family, mean=23.4, variance=51.0)
    arr = pmf_array(family, mean=23.4, variance=51.0, kmax=120)
    for k in (0, 1, 10, 23, 24, 40, 80):
        assert arr[k] == pytest.approx(pred.pmf(k), abs=1e-12)
    assert arr.sum() == pytest.approx(1.0, abs=1e-6)


def test_resolve_family_matches_build_predictive() -> None:
    for mean, var in ((20, 40), (20, 20.5), (8, 7), (30, 31.4)):
        built = build_predictive(
            DistributionFamily.AUTO, mean=mean, variance=var, history_values=[]
        )
        assert resolve_family(DistributionFamily.AUTO, mean, var) is built.family
        nb = build_predictive(
            DistributionFamily.NEGATIVE_BINOMIAL, mean=mean, variance=var, history_values=[]
        )
        assert resolve_family(DistributionFamily.NEGATIVE_BINOMIAL, mean, var) is nb.family


# --- eligibility and chronology ------------------------------------------------------
def test_candidates_follow_eligibility_policy(loaded: Session) -> None:
    history, _ = load_history(loaded)
    cfg = BacktestConfig(seasons=[2024], min_prior_games=10)
    cands, excl = enumerate_candidates(history, cfg)
    # 6 players x 12 games; first 10 of each are ineligible
    assert len(cands) == 6 * 2 and excl["insufficient_prior_games"] == 6 * 10
    assert all(idx >= 10 for _pid, idx in cands)
    other = enumerate_candidates(history, BacktestConfig(seasons=[2019]))[0]
    assert other == []


def test_zero_minute_targets_are_excluded_and_counted(tmp_path: Path) -> None:
    rows = [*HISTORY, "2024-11-27,P1,Walk Forward,BOS,MIA,home,0:00,0,0,0"]
    s = _db(tmp_path, "zero", rows)
    history, _ = load_history(s)
    cands, excl = enumerate_candidates(history, BacktestConfig(seasons=[2024], min_prior_games=3))
    assert excl["target_zero_minutes"] == 1
    assert len(cands) == len(HISTORY) - 3


def test_history_is_chronological_regardless_of_insertion_order(tmp_path: Path) -> None:
    shuffled = [
        HISTORY[5],
        HISTORY[0],
        HISTORY[9],
        HISTORY[2],
        *HISTORY[10:],
        HISTORY[1],
        HISTORY[3],
        HISTORY[4],
        HISTORY[6],
        HISTORY[7],
        HISTORY[8],
    ]
    s = _db(tmp_path, "shuffled", shuffled)
    history, _ = load_history(s)
    dates = [r.game_date for r in next(iter(history.values()))]
    assert dates == sorted(dates)


def test_backtest_prediction_equals_live_predict(tmp_path: Path) -> None:
    """The engine must reproduce exactly what predict() would have said before each game."""
    s = _db(tmp_path, "live", HISTORY)
    cfg = BacktestConfig(
        seasons=[2024], stats=[StatType.POINTS], min_prior_games=3, include_empirical=False
    )
    result = run_backtest(s, cfg)
    pid = find_players(s, "Walk Forward")[0].id
    for obs in result.observations:
        for model in cfg.models:
            rec = next(
                p
                for p in result.predictions
                if p.obs_id == obs.obs_id and p.model == model and p.requested_family == "auto"
            )
            live = predict(
                s,
                PredictionRequest(
                    player_id=pid,
                    stat_type=StatType.POINTS,
                    line=obs.lines[2],
                    as_of_date=date.fromisoformat(obs.game_date),
                    model_name=model,
                ),
            )
            assert rec.projection == pytest.approx(live.model.projection)
            assert rec.family == live.distribution.family.value
            assert rec.p_more[2] == pytest.approx(live.probabilities.more, abs=1e-9)
            assert obs.prior_games == live.sample_size


def test_target_and_future_games_do_not_leak(tmp_path: Path) -> None:
    base = _db(tmp_path, "base", HISTORY)
    # same history, but the 8th game's outcome is wildly different and two future games are appended
    altered_rows = list(HISTORY)
    altered_rows[7] = altered_rows[7].replace(",28,5,4", ",99,30,20")
    altered_rows += [
        "2024-12-01,P1,Walk Forward,BOS,MIA,home,30:00,70,5,4",
        "2024-12-03,P1,Walk Forward,BOS,NYK,away,30:00,0,0,0",
    ]
    altered = _db(tmp_path, "altered", altered_rows)
    cfg = BacktestConfig(
        seasons=[2024], stats=[StatType.POINTS], min_prior_games=3, date_to=date(2024, 11, 15)
    )
    a, b = run_backtest(base, cfg), run_backtest(altered, cfg)
    # Observations up to and including 2024-11-15 (the altered game) must have identical
    # predictions...
    preds_a = {
        (o.game_date, p.model, p.requested_family): p
        for o in a.observations
        for p in a.predictions
        if p.obs_id == o.obs_id
    }
    preds_b = {
        (o.game_date, p.model, p.requested_family): p
        for o in b.observations
        for p in b.predictions
        if p.obs_id == o.obs_id
    }
    assert preds_a.keys() == preds_b.keys()
    for key, pa in preds_a.items():
        pb = preds_b[key]
        assert pa.projection == pytest.approx(pb.projection) and pa.p_more == pytest.approx(
            pb.p_more
        )
    # ...while the altered target's recorded actual differs (scoring only), proving the
    # actual is not an input.
    obs_a = next(o for o in a.observations if o.game_date == "2024-11-15")
    obs_b = next(o for o in b.observations if o.game_date == "2024-11-15")
    assert (obs_a.actual, obs_b.actual) == (28, 99)
    assert obs_a.lines == obs_b.lines  # lines are pregame-only too


def test_equal_cohorts_determinism_and_sampling(loaded: Session) -> None:
    cfg = BacktestConfig(seasons=[2024], min_prior_games=3, max_observations=5, seed=11)
    r1, r2 = run_backtest(loaded, cfg), run_backtest(loaded, cfg)
    assert [o.player_id for o in r1.observations] == [o.player_id for o in r2.observations]
    assert [p.p_more for p in r1.predictions] == [p.p_more for p in r2.predictions]
    assert r1.exclusions["sampled_out_by_max_observations"] == r1.candidates - 5
    # every model/family predicted every observation (equal cohorts), empirical missing (<15 games)
    n_obs = len(r1.observations)
    for model in cfg.models:
        for fam in cfg.families:
            assert (
                sum(
                    1
                    for p in r1.predictions
                    if p.model == model and p.requested_family == fam.value
                )
                == n_obs
            )
    assert r1.missing[f"{EMPIRICAL_MODEL_NAME}/empirical"] == n_obs
    different = run_backtest(loaded, cfg.model_copy(update={"seed": 12}))
    assert [
        o.obs_id for o in different.observations
    ] != [] and different.candidates == r1.candidates


def test_probabilities_valid_and_interval_consistent(
    loaded: Session, small_config: BacktestConfig
) -> None:
    result = run_backtest(loaded, small_config)
    assert result.observations
    for p in result.predictions:
        assert all(0.0 <= x <= 1.0 for x in p.p_more)
        assert list(p.p_more) == sorted(p.p_more, reverse=True)  # higher line -> lower P(more)
        assert p.q_lo <= p.q_hi and p.nll >= 0
        obs = next(o for o in result.observations if o.obs_id == p.obs_id)
        assert p.covered == (p.q_lo <= obs.actual <= p.q_hi)


def test_write_read_analyse_and_report(
    loaded: Session, small_config: BacktestConfig, tmp_path: Path
) -> None:
    result = run_backtest(loaded, small_config)
    run_dir = tmp_path / "run"
    write_run(result, run_dir, database_url="sqlite:///test")
    meta, observations, predictions = read_run(run_dir)
    assert meta["observations"] == len(result.observations) == len(observations)
    assert len(predictions) == len(result.predictions)
    assert meta["model_versions"]["ewma"] == MODELS["ewma"].version
    cmp = analyse(observations, predictions, small_config.line_offsets, resamples=50)
    rows = {(r.model, r.family): r for r in cmp.rows}
    assert len(rows) == len(small_config.models) * len(
        small_config.families
    )  # empirical had <15 games
    assert all(r.n == cmp.cohort_sizes["points"] for r in cmp.rows)
    assert all(np.isfinite(r.mae) and 0 <= r.coverage <= 1 for r in cmp.rows)
    assert ("points", "ewma") in cmp.mae_ci
    built = build_report(run_dir, charts=True, resamples=50)
    assert built.cohort_sizes == cmp.cohort_sizes
    for name in ("summary.md", "comparison.csv", "strata.csv", "reliability.csv"):
        assert (run_dir / name).is_file()
    assert (run_dir / "charts" / "reliability_points.png").is_file()
    text = (run_dir / "summary.md").read_text()
    assert "synthetic evaluation thresholds" in text and "not PrizePicks lines" in text


def test_no_eligible_observations(loaded: Session) -> None:
    result = run_backtest(loaded, BacktestConfig(seasons=[2024], min_prior_games=50))
    assert result.observations == [] and result.predictions == []
    assert result.exclusions["insufficient_prior_games"] == 72
