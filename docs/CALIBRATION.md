# Calibration artifacts

`predict()` applies a versioned isotonic calibration map to its raw More/Less probabilities
when, and only when, an artifact exists for the exact stat, model name, model version and
requested distribution family. Everything else stays uncalibrated and says why.

## Artifact format

One JSON file per (stat, model, family) in a directory (`PPP_CALIBRATION_DIR`, default
`artifacts/calibration`). Fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | 1; loading refuses other versions |
| `artifact_id` | `isotonic.points.ewma-1.0.auto.fit2024-25.<hash8>`; the hash covers the fitted map |
| `stat`, `model_name`, `model_version`, `family` | the exact configuration the map was fitted for |
| `method`, `x`, `y` (or `a`, `b`) | isotonic block edges and values, or Platt parameters |
| `n_fit`, `fit_seasons`, `fit_runs`, `data_source`, `data_latest_game_date`, `fitted_at` | provenance |
| `validation_status` | `held_out_validated` or `in_sample_only` |
| `held_out` | metrics on a *different* run when validated (Brier, log loss, ECE before/after) |
| `line_policy`, `notes` | what the map calibrates and what it does not claim |

Two artifact sets are committed:

| Directory | Fitted on | Validated on | Use |
| --- | --- | --- | --- |
| `artifacts/calibration/` (default) | 2024-25 validation run | 2025-26 held-out run; ECE 0.032→0.002 (pts), 0.013→0.006 (reb), 0.018→0.004 (ast) | the Phase 3 result, reproduced exactly |
| `artifacts/calibration_operational_2026-27/` | 2024-25 + 2025-26 (253,885 pairs) | none; `in_sample_only` | an operational refit for the 2026-27 season; point `PPP_CALIBRATION_DIR` at it to use it |

The default stays the validated set because its out-of-sample behaviour is measured.
The operational refit uses more and more recent data but its reliability has not been
tested on games it did not see. Switching is a documented choice, not a silent upgrade,
and prospective monitoring (`ppp monitor`) is how the refit earns trust.

## How a probability is calibrated

Artifacts are fitted on half-point synthetic lines, where a push is impossible, so the
mapped quantity is P(more | no push). At prediction time:

```
cond        = p_more / (p_more + p_less)
cond_cal    = isotonic(cond)
p_more_cal  = cond_cal * (p_more + p_less)
p_less_cal  = (1 - cond_cal) * (p_more + p_less)
p_push_cal  = p_push                              (unchanged, from the count distribution)
```

The three calibrated values sum to 1 by construction; More and Less are never calibrated
separately. Raw probabilities are always returned alongside.

At integer lines this is an extrapolation: the artifact was fitted where pushes cannot
occur, and the held-out evaluation covered only half-point lines. `predict()` therefore
adds the `calibration_integer_line_unvalidated` warning whenever a calibrated result is
produced at an integer threshold. Validating the conditional map at integer lines needs
a backtest with integer offsets, which is a small Phase 4B task.

## Support (Phase 4C)

Each artifact also records its training domain: the 0.5th–99.5th percentiles of the raw
conditional probability it was fitted on and the number of training pairs in 20 equal bins
(`ppp annotate-calibration-support`; the fitted map and artifact id are unchanged). At
prediction time the map is applied only when the raw probability lies inside that range
*and* its bin holds at least 200 validated observations. Otherwise the raw estimate is
shown with the headline "Calibration outside validated range" or "Insufficient calibration
evidence" and the reason. For the committed points artifact the supported range is raw
0.126–0.844: a threshold far below a player's median (raw P(more) 0.95) is reported, not
mapped to 1.000. Probabilities are never displayed as exactly 1.000 or 0.000.

## Fallback

`probabilities_calibrated` is `None`, `calibration_status` starts with `uncalibrated:` and
the `calibration_unavailable` warning is added when no artifact matches (other model,
other family, other model version), when the directory is empty or missing, or when the
request sets `calibrate=False`. A corrupt or wrong-schema file raises at load time rather
than silently applying nothing.

## What calibration here means

Fitted and validated against synthetic thresholds built from each player's own history
(trailing-10 median ± offsets). It establishes that the model's probabilities track
observed frequencies at those thresholds, after the map, on a held-out season. It does
**not** establish calibration against PrizePicks or sportsbook lines, which embed market
information; no such validation exists in this repository.

## Refitting

```bash
uv run ppp fit-calibrators --fit-run data/backtests/val_2024-25 \
    --eval-run data/backtests/test_2025-26 --out artifacts/calibration
uv run ppp fit-calibrators --fit-run data/backtests/val_2024-25 \
    --fit-run data/backtests/test_2025-26 --out artifacts/calibration_operational_2026-27
```

Fitting needs at least 2,000 (probability, outcome) pairs per stat; below that the
artifact is not written and the reason is printed. The evaluation run must differ from
every fit run.
