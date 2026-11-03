from __future__ import annotations

import numpy as np
import pytest

from prizepicks_predictor.backtest.calibration import IsotonicCalibrator, PlattCalibrator
from prizepicks_predictor.backtest.metrics import expected_calibration_error, log_loss


def _overconfident_sample(n: int = 20000, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """True probability t; reported probability pushed away from 0.5 (overconfident)."""
    rng = np.random.default_rng(seed)
    t = rng.uniform(0.05, 0.95, n)
    y = (rng.uniform(size=n) < t).astype(float)
    logit = np.log(t / (1 - t))
    p = 1 / (1 + np.exp(-2.0 * logit))  # sharpened => overconfident
    return p, y


def test_platt_recovers_overconfidence() -> None:
    p, y = _overconfident_sample()
    cal = PlattCalibrator.fit(p, y)
    assert cal.a == pytest.approx(0.5, abs=0.05)  # undoes the x2 sharpening
    assert abs(cal.b) < 0.05
    q = cal.apply(p)
    assert log_loss(q, y) < log_loss(p, y)
    assert expected_calibration_error(q, y) < expected_calibration_error(p, y)


def test_isotonic_is_monotone_and_improves_reliability() -> None:
    p, y = _overconfident_sample(seed=3)
    cal = IsotonicCalibrator.fit(p, y)
    assert np.all(np.diff(cal.y) >= -1e-12)
    q = cal.apply(p)
    order = np.argsort(p)
    assert np.all(np.diff(q[order]) >= -1e-12)
    assert expected_calibration_error(q, y) < expected_calibration_error(p, y)
    assert q.min() >= 0.0 and q.max() <= 1.0


def test_isotonic_pav_small_case() -> None:
    cal = IsotonicCalibrator.fit([0.1, 0.2, 0.3, 0.4], [1, 0, 0, 1])
    # violators pooled: (1,0,0) -> 1/3 then 1
    assert cal.y.tolist() == pytest.approx([1 / 3, 1.0])
    assert cal.apply([0.0, 0.25, 0.9]).tolist() == pytest.approx([1 / 3, 1 / 3, 1.0])


def test_calibrators_fit_on_validation_only_apply_elsewhere() -> None:
    p_fit, y_fit = _overconfident_sample(seed=5)
    p_eval, y_eval = _overconfident_sample(seed=6)
    cal = PlattCalibrator.fit(p_fit, y_fit)
    assert cal.n_fit == len(p_fit)
    # applying to new data still helps: the fix generalises because the miscalibration is systematic
    assert log_loss(cal.apply(p_eval), y_eval) < log_loss(p_eval, y_eval)
