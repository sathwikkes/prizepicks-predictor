"""Opponent pace and defence context, computed from team box scores strictly before a date.

Definitions (per team-game, from NBA.com team logs via SportsDataverse):
    possessions = FGA - OREB + TOV + 0.44 * FTA
    pace48      = possessions * 48 / (team minutes / 5)        (overtime-normalised)
    def_rating  = points allowed * 100 / opponent possessions   (points allowed per 100)

Opponent features for a target game use the opponent's last ``window`` games in the same
season before the target date, shrunk toward the league average of the trailing 30 days:
    rel = (n * team_mean + k * league_mean) / ((n + k) * league_mean)
so a team with no history has rel = 1.0 and a full window dominates the prior (k = 5).
Everything is pregame-known; no target-game row is ever included.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, ConfigDict, Field

SHRINK_K = 5.0
WINDOW = 10
LEAGUE_DAYS = 30


@dataclass(frozen=True)
class TeamGameLine:
    game_date: date
    season_start_year: int
    possessions: float
    pace48: float
    points_allowed: float
    opp_possessions: float


def possessions(fga: float, oreb: float, tov: float, fta: float) -> float:
    return max(fga - oreb + tov + 0.44 * fta, 1.0)


def pace48(poss: float, team_minutes: float) -> float:
    return poss * 48.0 / max(team_minutes / 5.0, 1.0)


class OpponentContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    opponent: str | None = None
    games_used: int = 0
    opp_pace48: float | None = None
    opp_def_rating: float | None = Field(default=None, description="points allowed per 100 poss")
    league_pace48: float | None = None
    league_def_rating: float | None = None
    opp_pace_rel: float = Field(default=1.0, description="shrunk ratio to league average")
    opp_def_rel: float = Field(
        default=1.0, description="shrunk ratio to league average; >1 = weaker defence"
    )
    available: bool = False


def _window(
    lines: Sequence[TeamGameLine], target: date, season: int, window: int
) -> list[TeamGameLine]:
    dates = [ln.game_date for ln in lines]
    cut = bisect_left(dates, target)
    prior = [ln for ln in lines[:cut] if ln.season_start_year == season]
    return prior[-window:]


def league_averages(
    all_lines: Sequence[TeamGameLine], target: date, *, days: int = LEAGUE_DAYS
) -> tuple[float | None, float | None]:
    lo = target - timedelta(days=days)
    rows = [ln for ln in all_lines if lo <= ln.game_date < target]
    if not rows:
        return None, None
    pace = sum(ln.pace48 for ln in rows) / len(rows)
    dr = sum(ln.points_allowed * 100.0 / ln.opp_possessions for ln in rows) / len(rows)
    return pace, dr


def build_opponent_context(
    opponent: str | None,
    opponent_lines: Sequence[TeamGameLine],
    *,
    league: tuple[float | None, float | None],
    target_date: date,
    season_start_year: int,
    window: int = WINDOW,
    k: float = SHRINK_K,
) -> OpponentContext:
    league_pace, league_dr = league
    if opponent is None or league_pace is None or league_dr is None:
        return OpponentContext(opponent=opponent)
    if any(ln.game_date >= target_date for ln in opponent_lines):
        raise ValueError("leakage: opponent line on or after the target date")
    recent = _window(opponent_lines, target_date, season_start_year, window)
    n = len(recent)
    if n == 0:
        return OpponentContext(
            opponent=opponent,
            games_used=0,
            league_pace48=league_pace,
            league_def_rating=league_dr,
            opp_pace_rel=1.0,
            opp_def_rel=1.0,
            available=True,
        )
    pace = sum(ln.pace48 for ln in recent) / n
    dr = sum(ln.points_allowed * 100.0 / ln.opp_possessions for ln in recent) / n
    pace_rel = (n * pace + k * league_pace) / ((n + k) * league_pace)
    dr_rel = (n * dr + k * league_dr) / ((n + k) * league_dr)
    return OpponentContext(
        opponent=opponent,
        games_used=n,
        opp_pace48=pace,
        opp_def_rating=dr,
        league_pace48=league_pace,
        league_def_rating=league_dr,
        opp_pace_rel=pace_rel,
        opp_def_rel=dr_rel,
        available=True,
    )
