"""Plotly figures for the dashboard. Pure functions over plain data."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import plotly.graph_objects as go

from prizepicks_predictor.prediction.distributions import pmf_array
from prizepicks_predictor.prediction.schemas import DistributionFamily, PredictionResult
from prizepicks_predictor.schemas.game_log import GameLogRow

STAT_COLORS = {
    "points": "#4C72B0",
    "rebounds": "#55A868",
    "assists": "#C44E52",
    "minutes": "#8172B2",
}


def game_log_chart(rows: Sequence[GameLogRow], stat: str, *, rolling: int = 10) -> go.Figure:
    """Per-game values (oldest to newest) with a trailing rolling mean."""
    ordered = sorted(rows, key=lambda r: r.game_date)
    xs = [r.game_date for r in ordered]
    ys = [float(getattr(r, stat)) for r in ordered]
    fig = go.Figure()
    fig.add_bar(
        x=xs,
        y=ys,
        name=stat,
        marker_color=STAT_COLORS.get(stat, "#444"),
        opacity=0.75,
        hovertemplate="%{x}<br>" + stat + ": %{y}<extra></extra>",
    )
    if len(ys) >= 2:
        roll = [float(np.mean(ys[max(0, i - rolling + 1) : i + 1])) for i in range(len(ys))]
        fig.add_scatter(
            x=xs,
            y=roll,
            mode="lines",
            name=f"trailing {rolling}-game mean",
            line={"color": "#222", "width": 2},
        )
    fig.update_layout(
        height=300,
        margin={"l": 30, "r": 10, "t": 30, "b": 30},
        title=stat.title(),
        legend={"orientation": "h"},
    )
    return fig


def distribution_chart(result: PredictionResult) -> go.Figure:
    """The predictive pmf with the line and 80% interval marked."""
    d = result.distribution
    family = DistributionFamily(d.family)
    if family is DistributionFamily.EMPIRICAL:
        return go.Figure(layout={"title": "Empirical distribution (pmf not reconstructed)"})
    variance = d.std**2
    kmax = int(min(300, max(40, d.mean + 6 * d.std + 5)))
    pmf = pmf_array(family, mean=d.mean, variance=variance, kmax=kmax)
    ks = np.arange(kmax + 1)
    lo, hi = result.interval_80
    colors = ["#4C72B0" if lo <= k <= hi else "#B0C4DE" for k in ks]
    fig = go.Figure()
    fig.add_bar(
        x=ks,
        y=pmf,
        marker_color=colors,
        name="P(X = k)",
        hovertemplate="k=%{x}<br>p=%{y:.4f}<extra></extra>",
    )
    fig.add_vline(
        x=result.line,
        line_dash="dash",
        line_color="#C44E52",
        annotation_text=f"line {result.line:g}",
    )
    fig.add_vline(
        x=result.model.projection,
        line_color="#222",
        annotation_text=f"proj {result.model.projection:.1f}",
        annotation_position="top left",
    )
    fig.update_layout(
        height=320,
        margin={"l": 30, "r": 10, "t": 40, "b": 30},
        xaxis_title=result.stat_type.value,
        yaxis_title="probability",
        showlegend=False,
        title=f"{d.family.value} predictive distribution (dark bars = 80% interval)",
    )
    return fig


def reliability_chart(
    rows: Sequence[dict[str, Any]], *, min_n: int = 30, title: str = "Reliability"
) -> go.Figure:
    """rows: reliability.csv rows for one stat (any number of model/family series)."""
    fig = go.Figure()
    fig.add_scatter(
        x=[0, 1], y=[0, 1], mode="lines", line={"dash": "dash", "color": "#999"}, name="perfect"
    )
    series: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        if int(r["n"]) < min_n:
            continue
        key = f"{r['model']}/{r['family']}" if "model" in r else r.get("method", "series")
        series.setdefault(key, []).append(
            (float(r["mean_predicted"]), float(r["observed_frequency"]))
        )
    for key, pts in series.items():
        pts.sort()
        fig.add_scatter(
            x=[p[0] for p in pts], y=[p[1] for p in pts], mode="lines+markers", name=key
        )
    fig.update_layout(
        height=380,
        title=title,
        xaxis_title="predicted P(more)",
        yaxis_title="observed frequency",
        xaxis_range=[0, 1],
        yaxis_range=[0, 1],
        margin={"l": 30, "r": 10, "t": 40, "b": 30},
    )
    return fig


def metric_bar_chart(
    rows: Sequence[dict[str, Any]], *, metric: str, family: str = "auto", title: str | None = None
) -> go.Figure:
    """One bar per model for a comparison.csv subset (single stat)."""
    picked = [r for r in rows if r.get("family", family) == family]
    picked.sort(key=lambda r: float(r[metric]))
    fig = go.Figure(
        go.Bar(
            x=[r["model"] for r in picked],
            y=[float(r[metric]) for r in picked],
            marker_color="#4C72B0",
            text=[f"{float(r[metric]):.3f}" for r in picked],
            textposition="outside",
        )
    )
    fig.update_layout(
        height=320,
        title=title or metric.upper(),
        margin={"l": 30, "r": 10, "t": 40, "b": 30},
        yaxis_title=metric,
    )
    return fig
