"""Plain-English explanations for diagnostic codes and probability formatting."""

from __future__ import annotations

WARNING_EXPLANATIONS: dict[str, str] = {
    "small_sample": "Fewer than 10 prior games: the projection rests on little evidence.",
    "season_fallback": (
        "No games yet this season; last season's games stand in for the season average."
    ),
    "window_crosses_season": "The recent-history window includes games from the previous season.",
    "team_change_in_window": (
        "The player changed teams within the recent window; the role may have shifted."
    ),
    "dnp_games_excluded": "Games with zero minutes were excluded from all averages.",
    "minutes_volatile": "Recent playing time varies a lot, so the projection is less reliable.",
    "empirical_fallback": (
        "Not enough history for the empirical distribution; a parametric one was used."
    ),
    "long_layoff": "More than two weeks since the player's last game.",
    "matchup_context_unavailable": (
        "This projection does not include verified opponent-specific adjustments "
        "(defence, pace, injuries)."
    ),
    "schedule_context_unavailable": (
        "No prior team game found, so rest and back-to-back context is unknown."
    ),
    "calibration_unavailable": (
        "No calibration artifact matches this model and distribution; "
        "the raw model probability is shown."
    ),
    "calibration_integer_line_unvalidated": (
        "Calibration was validated on half-point thresholds. At a whole-number threshold the push "
        "probability is kept from the model and the More/Less split is an extrapolation."
    ),
    "calibration_outside_support": (
        "The raw probability is outside the range where calibration was validated, so the raw "
        "estimate is shown instead."
    ),
    "calibration_insufficient_evidence": (
        "Too few validated observations near this probability, "
        "so the raw estimate is shown instead."
    ),
    "threshold_far_from_recent_median": (
        "This threshold is far from the player's recent median; calibration was validated only "
        "within about 2 of it."
    ),
}


def explain_warning(code: str) -> str:
    return WARNING_EXPLANATIONS.get(code, code.replace("_", " "))


def format_probability(p: float | None) -> str:
    """Never print a probability as exactly 1.000 or 0.000."""
    if p is None:
        return "n/a"
    if p >= 0.9995:
        return "> 0.999"
    if p <= 0.0005:
        return "< 0.001"
    return f"{p:.3f}"
