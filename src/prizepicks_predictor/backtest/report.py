"""Persisting a backtest and turning it into comparison tables and charts.

Layout of a run directory::

    run.json                 config, provenance, timings, exclusions, missing predictions
    observations.csv.gz      one row per (player, game, stat) with actual outcome and lines
    predictions.csv.gz       one row per (observation, model, family)
    comparison.csv           per (stat, model, family) metrics on the common cohort
    strata.csv               the same metrics stratified by season / prior games / minutes / role
    reliability.csv          reliability-table bins per (stat, model, family)
    summary.md               human-readable summary
    charts/*.png             reliability diagrams, error histograms, coverage bars
"""

from __future__ import annotations

import csv
import gzip
import json
import platform
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from prizepicks_predictor import __version__
from prizepicks_predictor.backtest.config import BacktestConfig, season_label
from prizepicks_predictor.backtest.engine import (
    EMPIRICAL_MODEL_NAME,
    BacktestResult,
    Observation,
    PredictionRecord,
)
from prizepicks_predictor.backtest.metrics import (
    BootstrapCI,
    ReliabilityBin,
    brier,
    expected_calibration_error,
    interval_coverage,
    log_loss,
    paired_bootstrap_mean_diff,
    point_metrics,
    reliability_table,
)

Array = NDArray[np.float64]
REFERENCE_MODEL = "season_average"
REFERENCE_FAMILY = "auto"


# --------------------------------------------------------------------------- persistence
def _obs_fields(config: BacktestConfig) -> list[str]:
    base = [f.name for f in Observation.__dataclass_fields__.values() if f.name != "lines"]
    return base + [f"line_{off:+d}" for off in config.line_offsets]


def _pred_fields(config: BacktestConfig) -> list[str]:
    base = [f.name for f in PredictionRecord.__dataclass_fields__.values() if f.name != "p_more"]
    return base + [f"p_more_{off:+d}" for off in config.line_offsets]


def write_run(result: BacktestResult, run_dir: Path, *, database_url: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = result.config
    with gzip.open(run_dir / "observations.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_obs_fields(cfg))
        for o in result.observations:
            d = asdict(o)
            lines = d.pop("lines")
            writer.writerow([*d.values(), *lines])
    with gzip.open(run_dir / "predictions.csv.gz", "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_pred_fields(cfg))
        for p in result.predictions:
            d = asdict(p)
            probs = d.pop("p_more")
            writer.writerow([*d.values(), *probs])
    meta = {
        "run_started_at": result.started_at.isoformat(),
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "package_version": __version__,
        "python": platform.python_version(),
        "database_url": database_url,
        "config": json.loads(cfg.model_dump_json()),
        "candidates": result.candidates,
        "observations": len(result.observations),
        "predictions": len(result.predictions),
        "exclusions": dict(result.exclusions),
        "missing_predictions": dict(result.missing),
        "model_versions": _model_versions(cfg),
        "lines_note": (
            "synthetic half-point evaluation lines (trailing median +/- offsets); "
            "not PrizePicks lines"
        ),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _model_versions(cfg: BacktestConfig) -> dict[str, str]:
    from prizepicks_predictor.prediction.models import MODELS

    return {name: MODELS[name].version for name in cfg.models}


def read_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    with gzip.open(run_dir / "observations.csv.gz", "rt", newline="", encoding="utf-8") as fh:
        observations = list(csv.DictReader(fh))
    with gzip.open(run_dir / "predictions.csv.gz", "rt", newline="", encoding="utf-8") as fh:
        predictions = list(csv.DictReader(fh))
    return meta, observations, predictions


# --------------------------------------------------------------------------- analysis
@dataclass
class Cohort:
    """Aligned arrays for one (stat, model, family) on the common observation set."""

    stat: str
    model: str
    family: str
    obs_ids: Array
    actual: Array
    projection: Array
    nll: Array
    q_lo: Array
    q_hi: Array
    p_more: Array  # shape (n_obs, n_lines)
    outcomes: Array  # shape (n_obs, n_lines): actual > line
    season: Array
    prior_games: Array
    minutes_recent: Array
    start_rate: Array
    rest_bucket: list[str]
    opp_def_rel: Array
    opp_pace_rel: Array


@dataclass
class ConfigRow:
    stat: str
    model: str
    family: str
    n: int
    n_lines: int
    mae: float
    rmse: float
    bias: float
    nll: float
    coverage: float
    width: float
    brier: float
    log_loss: float
    ece: float


def build_cohorts(
    observations: list[dict[str, str]], predictions: list[dict[str, str]], offsets: list[int]
) -> tuple[dict[tuple[str, str, str], Cohort], dict[str, int], dict[str, dict[str, int]]]:
    """Group predictions by (stat, model, family) and restrict each stat to observations
    predicted by every *model* configuration, so model comparisons are on identical cohorts.

    The model-independent empirical reference needs more history than the models, so it
    is evaluated on its own subset of the common cohort and its ``n`` is reported
    separately rather than shrinking everyone else's cohort.

    Returns cohorts, cohort size per stat, and per-stat counts of dropped observations
    per configuration (those a configuration could not predict).
    """
    obs_by_id = {int(o["obs_id"]): o for o in observations}
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for p in predictions:
        key = (obs_by_id[int(p["obs_id"])]["stat"], p["model"], p["requested_family"])
        grouped[key].append(p)

    cohorts: dict[tuple[str, str, str], Cohort] = {}
    common_sizes: dict[str, int] = {}
    dropped: dict[str, dict[str, int]] = defaultdict(dict)
    stats = sorted({o["stat"] for o in observations})
    for stat in stats:
        keys = [k for k in grouped if k[0] == stat]
        id_sets = [{int(p["obs_id"]) for p in grouped[k]} for k in keys]
        model_sets = [
            ids for k, ids in zip(keys, id_sets, strict=True) if k[1] != EMPIRICAL_MODEL_NAME
        ]
        common = set.intersection(*model_sets) if model_sets else set()
        common_sizes[stat] = len(common)
        all_ids = {int(o["obs_id"]) for o in observations if o["stat"] == stat}
        for k, ids in zip(keys, id_sets, strict=True):
            dropped[stat][f"{k[1]}/{k[2]}"] = len(all_ids - ids)
        for k in keys:
            rows = sorted(
                (p for p in grouped[k] if int(p["obs_id"]) in common),
                key=lambda r: int(r["obs_id"]),
            )
            if not rows:
                continue
            obs = [obs_by_id[int(r["obs_id"])] for r in rows]
            lines = np.array([[float(o[f"line_{off:+d}"]) for off in offsets] for o in obs])
            actual = np.array([float(o["actual"]) for o in obs])
            cohorts[k] = Cohort(
                stat=stat,
                model=k[1],
                family=k[2],
                obs_ids=np.array([int(r["obs_id"]) for r in rows], dtype=np.float64),
                actual=actual,
                projection=np.array([float(r["projection"]) for r in rows]),
                nll=np.array([float(r["nll"]) for r in rows]),
                q_lo=np.array([float(r["q_lo"]) for r in rows]),
                q_hi=np.array([float(r["q_hi"]) for r in rows]),
                p_more=np.array([[float(r[f"p_more_{off:+d}"]) for off in offsets] for r in rows]),
                outcomes=(actual[:, None] > lines).astype(np.float64),
                season=np.array([float(o["season"]) for o in obs]),
                prior_games=np.array([float(o["prior_games"]) for o in obs]),
                minutes_recent=np.array([float(o["minutes_recent"] or "nan") for o in obs]),
                start_rate=np.array([float(o["prior_start_rate"] or "nan") for o in obs]),
                rest_bucket=[o.get("rest_bucket", "unknown") for o in obs],
                opp_def_rel=np.array([float(o.get("opp_def_rel") or "nan") for o in obs]),
                opp_pace_rel=np.array([float(o.get("opp_pace_rel") or "nan") for o in obs]),
            )
    return cohorts, common_sizes, dropped


def config_row(c: Cohort, mask: NDArray[np.bool_] | None = None) -> ConfigRow:
    m = np.ones(len(c.actual), dtype=bool) if mask is None else mask
    pm = point_metrics(c.projection[m], c.actual[m])
    cov, width = interval_coverage(c.q_lo[m], c.q_hi[m], c.actual[m])
    p = c.p_more[m].ravel()
    y = c.outcomes[m].ravel()
    return ConfigRow(
        stat=c.stat,
        model=c.model,
        family=c.family,
        n=pm.n,
        n_lines=len(p),
        mae=pm.mae,
        rmse=pm.rmse,
        bias=pm.bias,
        nll=float(np.mean(c.nll[m])) if pm.n else float("nan"),
        coverage=cov,
        width=width,
        brier=brier(p, y),
        log_loss=log_loss(p, y),
        ece=expected_calibration_error(p, y),
    )


def strata_rows(c: Cohort) -> list[tuple[str, str, ConfigRow]]:
    out: list[tuple[str, str, ConfigRow]] = []
    for season in sorted(set(c.season.tolist())):
        out.append(("season", season_label(int(season)), config_row(c, c.season == season)))
    buckets: list[tuple[str, Array, list[tuple[str, float, float]]]] = [
        ("prior_games", c.prior_games, [("10-29", 10, 30), ("30-79", 30, 80), ("80+", 80, 1e9)]),
        ("minutes_recent", c.minutes_recent, [("<20", 0, 20), ("20-30", 20, 30), ("30+", 30, 100)]),
        ("role_by_prior_starts", c.start_rate, [("bench", 0.0, 0.5), ("starter", 0.5, 1.01)]),
    ]
    for stratum, values, ranges in buckets:
        for label, lo, hi in ranges:
            mask = (values >= lo) & (values < hi)
            if mask.any():
                out.append((stratum, label, config_row(c, mask)))
    for stratum, values in (("opp_defense", c.opp_def_rel), ("opp_pace", c.opp_pace_rel)):
        finite = values[np.isfinite(values)]
        if len(finite) >= 100:
            lo_q, hi_q = np.quantile(finite, [0.2, 0.8])
            for label, mask in (
                ("bottom20", values <= lo_q),
                ("middle", (values > lo_q) & (values < hi_q)),
                ("top20", values >= hi_q),
            ):
                if mask.any():
                    out.append((stratum, label, config_row(c, mask)))
    buckets_rest = np.array(c.rest_bucket)
    for label in ("back_to_back", "three_in_four", "normal", "long_rest"):
        mask = buckets_rest == label
        if mask.any():
            out.append(("rest", label, config_row(c, mask)))
    return out


@dataclass
class Comparison:
    rows: list[ConfigRow]
    strata: list[tuple[str, str, ConfigRow]]
    reliability: dict[tuple[str, str, str], list[ReliabilityBin]]
    mae_ci: dict[tuple[str, str], BootstrapCI]  # (stat, model) vs REFERENCE_MODEL, |error| diff
    brier_ci: dict[tuple[str, str, str], BootstrapCI]  # (stat, model, family) vs reference config
    cohort_sizes: dict[str, int]
    dropped: dict[str, dict[str, int]]


def analyse(
    observations: list[dict[str, str]],
    predictions: list[dict[str, str]],
    offsets: list[int],
    *,
    seed: int = 0,
    resamples: int = 1000,
) -> Comparison:
    cohorts, sizes, dropped = build_cohorts(observations, predictions, offsets)
    rows = [config_row(c) for c in cohorts.values()]
    strata = [item for c in cohorts.values() for item in strata_rows(c)]
    reliability = {
        k: reliability_table(c.p_more.ravel(), c.outcomes.ravel()) for k, c in cohorts.items()
    }

    mae_ci: dict[tuple[str, str], BootstrapCI] = {}
    brier_ci: dict[tuple[str, str, str], BootstrapCI] = {}
    for (stat, model, family), c in cohorts.items():
        ref = cohorts.get((stat, REFERENCE_MODEL, REFERENCE_FAMILY))
        if ref is None or len(ref.actual) != len(c.actual) or model == EMPIRICAL_MODEL_NAME:
            continue
        if family == REFERENCE_FAMILY and model != REFERENCE_MODEL:
            mae_ci[(stat, model)] = paired_bootstrap_mean_diff(
                np.abs(c.projection - c.actual),
                np.abs(ref.projection - ref.actual),
                seed=seed,
                resamples=resamples,
            )
        if (model, family) != (REFERENCE_MODEL, REFERENCE_FAMILY):
            per_obs_brier = ((c.p_more - c.outcomes) ** 2).mean(axis=1)
            ref_brier = ((ref.p_more - ref.outcomes) ** 2).mean(axis=1)
            brier_ci[(stat, model, family)] = paired_bootstrap_mean_diff(
                per_obs_brier, ref_brier, seed=seed, resamples=resamples
            )
    return Comparison(rows, strata, reliability, mae_ci, brier_ci, sizes, dropped)


# --------------------------------------------------------------------------- output
def _fmt(x: float, digits: int = 3) -> str:
    return "n/a" if x != x else f"{x:.{digits}f}"


def write_tables(cmp: Comparison, run_dir: Path) -> None:
    with (run_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(list(ConfigRow.__dataclass_fields__))
        for r in sorted(cmp.rows, key=lambda r: (r.stat, r.model, r.family)):
            w.writerow(list(asdict(r).values()))
    with (run_dir / "strata.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["stratum", "bucket", *ConfigRow.__dataclass_fields__])
        for stratum, bucket, r in cmp.strata:
            w.writerow([stratum, bucket, *asdict(r).values()])
    with (run_dir / "reliability.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "stat",
                "model",
                "family",
                "bin_lo",
                "bin_hi",
                "n",
                "mean_predicted",
                "observed_frequency",
            ]
        )
        for (stat, model, family), bins in sorted(cmp.reliability.items()):
            for b in bins:
                w.writerow(
                    [stat, model, family, b.lo, b.hi, b.n, b.mean_predicted, b.observed_frequency]
                )


def summary_markdown(cmp: Comparison, meta: dict[str, Any]) -> str:
    cfg = meta["config"]
    seasons = ", ".join(season_label(int(s)) for s in cfg["seasons"])
    out = [
        f"# Backtest summary: {seasons}",
        "",
        f"Run started {meta['run_started_at']} · {meta['elapsed_seconds']}s · "
        f"package {meta['package_version']}",
        "",
        "Lines are **synthetic evaluation thresholds** (trailing-{window}-game median ± offsets, "
        "pregame-only, identical for every model). They are not PrizePicks lines and say nothing "
        "about wagering.".format(window=cfg["line_window"]),
        "",
        "## Cohort",
        "",
        f"- candidate player-games: {meta['candidates']}; "
        f"observations evaluated: {meta['observations']}",
        f"- exclusions: {meta['exclusions']}",
        f"- missing predictions (configuration → count): {meta['missing_predictions'] or 'none'}",
        "- common cohort per stat (observations every configuration predicted): "
        f"{cmp.cohort_sizes}",
        "",
    ]
    for stat in sorted({r.stat for r in cmp.rows}):
        rows = sorted((r for r in cmp.rows if r.stat == stat), key=lambda r: (r.model, r.family))
        out += [
            f"## {stat}",
            "",
            f"n = {cmp.cohort_sizes.get(stat, 0)} player-games, "
            f"{rows[0].n_lines} line-observations per configuration",
            "",
        ]
        out.append(
            "| model | family | MAE | RMSE | bias | NLL | 80% cov | width | Brier | "
            "log loss | ECE |"
        )
        out.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in rows:
            out.append(
                f"| {r.model} | {r.family} | {_fmt(r.mae)} | {_fmt(r.rmse)} | {_fmt(r.bias)} | "
                f"{_fmt(r.nll)} | {_fmt(r.coverage)} | {_fmt(r.width, 1)} | {_fmt(r.brier, 4)} | "
                f"{_fmt(r.log_loss, 4)} | {_fmt(r.ece, 4)} |"
            )
        best_mae = min(rows, key=lambda r: r.mae)
        best_rmse = min(rows, key=lambda r: r.rmse)
        best_nll = min(rows, key=lambda r: r.nll)
        best_brier = min(rows, key=lambda r: r.brier)
        best_ece = min(rows, key=lambda r: r.ece)
        out += [
            "",
            f"- best MAE: **{best_mae.model}** ({_fmt(best_mae.mae)}); "
            f"best RMSE: **{best_rmse.model}** ({_fmt(best_rmse.rmse)})",
            f"- best NLL: **{best_nll.model}/{best_nll.family}** ({_fmt(best_nll.nll)}); "
            f"best Brier: **{best_brier.model}/{best_brier.family}** ({_fmt(best_brier.brier, 4)})",
            f"- lowest ECE (reliability): **{best_ece.model}/{best_ece.family}** "
            f"({_fmt(best_ece.ece, 4)}); inspect the reliability diagram before calling "
            "anything calibrated",
            "",
            f"MAE difference vs {REFERENCE_MODEL} (paired bootstrap 95% CI; negative = better):",
            "",
        ]
        for (s, model), ci in sorted(cmp.mae_ci.items()):
            if s == stat:
                flag = "meaningful" if ci.excludes_zero else "within sampling noise"
                out.append(f"- {model}: {ci.estimate:+.3f} [{ci.lo:+.3f}, {ci.hi:+.3f}] ({flag})")
        out += [
            "",
            f"Brier difference vs {REFERENCE_MODEL}/{REFERENCE_FAMILY} (paired bootstrap 95% CI):",
            "",
        ]
        for (s, model, family), ci in sorted(cmp.brier_ci.items()):
            if s == stat:
                flag = "meaningful" if ci.excludes_zero else "within sampling noise"
                out.append(
                    f"- {model}/{family}: {ci.estimate:+.4f} [{ci.lo:+.4f}, {ci.hi:+.4f}] ({flag})"
                )
        out.append("")
    return "\n".join(out)


def write_charts(
    cmp: Comparison, run_dir: Path, *, families: tuple[str, ...] = ("auto",)
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    chart_dir = run_dir / "charts"
    chart_dir.mkdir(exist_ok=True)
    written: list[Path] = []
    stats = sorted({k[0] for k in cmp.reliability})
    for stat in stats:
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect")
        for (s, model, family), bins in sorted(cmp.reliability.items()):
            if s != stat or family not in families:
                continue
            xs = [b.mean_predicted for b in bins if b.n >= 30]
            ys = [b.observed_frequency for b in bins if b.n >= 30]
            ax.plot(xs, ys, marker="o", label=f"{model}/{family}")
        ax.set_xlabel("predicted P(more)")
        ax.set_ylabel("observed frequency")
        ax.set_title(f"Reliability: {stat} (synthetic lines, bins with n>=30)")
        ax.legend(fontsize=8)
        path = chart_dir / f"reliability_{stat}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=110)
        plt.close(fig)
        written.append(path)

        rows = [r for r in cmp.rows if r.stat == stat and r.family in families]
        if rows:
            fig, ax = plt.subplots(figsize=(7, 4))
            labels = [f"{r.model}\n{r.family}" for r in rows]
            ax.bar(labels, [r.coverage for r in rows], color="#4C72B0")
            ax.axhline(0.8, color="k", ls="--", lw=1)
            ax.set_ylim(0, 1)
            ax.set_ylabel("80% interval coverage")
            ax.set_title(f"Interval coverage: {stat}")
            ax.tick_params(axis="x", labelsize=7)
            path = chart_dir / f"coverage_{stat}.png"
            fig.tight_layout()
            fig.savefig(path, dpi=110)
            plt.close(fig)
            written.append(path)
    return written


def write_error_histograms(
    observations: list[dict[str, str]],
    predictions: list[dict[str, str]],
    run_dir: Path,
    *,
    model: str,
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    obs_by_id = {int(o["obs_id"]): o for o in observations}
    errors: dict[str, list[float]] = defaultdict(list)
    seen: set[int] = set()
    for p in predictions:
        if p["model"] != model or int(p["obs_id"]) in seen:
            continue
        seen.add(int(p["obs_id"]))
        o = obs_by_id[int(p["obs_id"])]
        errors[o["stat"]].append(float(p["projection"]) - float(o["actual"]))
    written: list[Path] = []
    for stat, errs in sorted(errors.items()):
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.hist(errs, bins=60, color="#55A868")
        ax.axvline(0, color="k", lw=1)
        ax.set_title(f"Forecast error ({model}): {stat}, n={len(errs)}")
        ax.set_xlabel("projection - actual")
        path = run_dir / "charts" / f"errors_{model}_{stat}.png"
        path.parent.mkdir(exist_ok=True)
        fig.tight_layout()
        fig.savefig(path, dpi=110)
        plt.close(fig)
        written.append(path)
    return written


def build_report(
    run_dir: Path, *, charts: bool = True, seed: int = 0, resamples: int = 1000
) -> Comparison:
    meta, observations, predictions = read_run(run_dir)
    offsets = [int(x) for x in meta["config"]["line_offsets"]]
    cmp = analyse(observations, predictions, offsets, seed=seed, resamples=resamples)
    write_tables(cmp, run_dir)
    (run_dir / "summary.md").write_text(summary_markdown(cmp, meta), encoding="utf-8")
    if charts:
        write_charts(cmp, run_dir)
        best = min(
            (r for r in cmp.rows if r.family == REFERENCE_FAMILY), key=lambda r: r.mae, default=None
        )
        if best is not None:
            write_error_histograms(observations, predictions, run_dir, model=best.model)
    meta["report_generated_at"] = datetime.now(UTC).isoformat()
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return cmp
