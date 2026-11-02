"""Synthetic evaluation lines.

We have no historical PrizePicks lines, so More/Less skill is measured against
*synthetic* half-point thresholds. The construction is:

* **pregame-only**: it uses only the player's games strictly before the target game;
* **model-independent**: every model is scored on the identical thresholds, so the
  comparison is of probability quality, not of each model's own median;
* **reproducible**: a deterministic function of the history.

Base line = floor(median of the last ``window`` played games) + 0.5, then offsets of
whole numbers keep every line on a half point (no pushes). Lines are clipped at 0.5.

These are NOT PrizePicks lines. Hit rates against them say nothing about wagering.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence


def base_line(values_desc: Sequence[float], window: int) -> float:
    """Half-point threshold at the trailing median of the most recent ``window`` values."""
    recent = list(values_desc[:window])
    if not recent:
        raise ValueError("no history for a synthetic line")
    return math.floor(statistics.median(recent)) + 0.5


def synthetic_lines(
    values_desc: Sequence[float], *, window: int, offsets: Sequence[int]
) -> list[float]:
    base = base_line(values_desc, window)
    return [max(0.5, base + off) for off in offsets]
