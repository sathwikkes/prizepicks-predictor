"""Schedule-derived pregame context: rest, back-to-backs, three-in-four, venue.

Everything here is known before tip-off: the target game's date and venue come from the
schedule, and the team's previous game dates come from completed games (or the schedule).
Nothing about the target game's result, minutes or lineup is used. ``build_schedule_context``
raises if any supplied date is not strictly before the target.

Rest is **team**-based (days since the team's previous game), not player-based, because a
player who sat out the previous game still travels and practises on the team's schedule.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field

LONG_REST_DAYS = 3


class ScheduleContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_date: date
    is_home: bool | None = Field(default=None, description="None when the venue is unknown")
    rest_days: int | None = Field(
        default=None, description="days since the team's previous game; None if unknown"
    )
    back_to_back: bool | None = None
    three_in_four: bool | None = Field(
        default=None, description="target is the team's 3rd game in 4 days"
    )
    games_last_7_days: int | None = None
    source: str = Field(
        default="games", description="where previous dates came from: games | schedule | none"
    )

    @property
    def rest_bucket(self) -> str:
        if self.rest_days is None:
            return "unknown"
        if self.rest_days <= 1:
            return "back_to_back"
        if self.three_in_four:
            return "three_in_four"
        if self.rest_days >= LONG_REST_DAYS:
            return "long_rest"
        return "normal"


def rest_days(team_dates_asc: Sequence[date], target: date) -> int | None:
    """Days between the team's last game strictly before ``target`` and ``target``."""
    idx = bisect_left(list(team_dates_asc), target)
    if idx == 0:
        return None
    return (target - team_dates_asc[idx - 1]).days


def games_in_window(team_dates_asc: Sequence[date], target: date, days: int) -> int:
    """Team games on dates in [target - days, target)."""
    lo = target - timedelta(days=days)
    return sum(1 for d in team_dates_asc if lo <= d < target)


def build_schedule_context(
    team_dates_before: Sequence[date],
    *,
    target_date: date,
    is_home: bool | None,
    source: str = "games",
) -> ScheduleContext:
    dates = sorted(set(team_dates_before))
    if dates and dates[-1] >= target_date:
        raise ValueError(f"leakage: team game on {dates[-1]} is not before target {target_date}")
    if not dates:
        return ScheduleContext(target_date=target_date, is_home=is_home, source="none")
    rest = rest_days(dates, target_date)
    last3 = games_in_window(dates, target_date, 3)  # games on the 3 days before the target
    return ScheduleContext(
        target_date=target_date,
        is_home=is_home,
        rest_days=rest,
        back_to_back=rest is not None and rest <= 1,
        three_in_four=last3 >= 2,
        games_last_7_days=games_in_window(dates, target_date, 7),
        source=source,
    )
