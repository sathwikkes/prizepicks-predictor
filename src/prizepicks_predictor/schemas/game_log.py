"""Provider-agnostic game-log record used as the ingestion contract.

Every provider adapter must emit ``NormalizedGameLog`` instances. The loader
only knows about this schema, so adding a provider never touches the database
code.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Upper bound on minutes in a single game. A 4-OT game is 68 minutes; this
#: leaves room for data quirks while still catching obviously broken values.
MAX_MINUTES = 80.0


def season_start_year_for(game_date: date) -> int:
    """Infer the season start year from a game date.

    NBA regular seasons begin in October. Games from January through September
    belong to the season that began the previous calendar year. The 2020-21
    season (started December 2020) and the 2019-20 bubble (ended October 2020)
    are handled correctly by this rule as well.
    """
    return game_date.year if game_date.month >= 10 else game_date.year - 1


class NormalizedGameLog(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=64)
    source_player_id: str = Field(min_length=1, max_length=64)
    source_game_id: str | None = Field(default=None, max_length=64)

    player_name: str = Field(min_length=1, max_length=128)
    team: str = Field(min_length=2, max_length=5, description="Team abbreviation, e.g. BOS")
    opponent: str = Field(min_length=2, max_length=5)
    is_home: bool
    game_date: date
    season_start_year: int | None = Field(default=None, ge=1946, le=2100)

    minutes: float = Field(ge=0, le=MAX_MINUTES)
    points: int = Field(ge=0, le=150)
    rebounds: int = Field(ge=0, le=60)
    assists: int = Field(ge=0, le=50)
    steals: int | None = Field(default=None, ge=0, le=20)
    blocks: int | None = Field(default=None, ge=0, le=20)
    turnovers: int | None = Field(default=None, ge=0, le=20)
    three_pointers_made: int | None = Field(default=None, ge=0, le=30)
    started: bool | None = None

    team_score: int | None = Field(default=None, ge=0)
    opponent_score: int | None = Field(default=None, ge=0)

    @field_validator("team", "opponent", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="before")
    @classmethod
    def _infer_season(cls, data: Any) -> Any:
        """Fill ``season_start_year`` from ``game_date`` when the provider omits it."""
        if isinstance(data, dict) and data.get("season_start_year") is None:
            game_date = data.get("game_date")
            if isinstance(game_date, str):
                try:
                    game_date = date.fromisoformat(game_date)
                except ValueError:
                    return data  # let the field validator report the bad date
            if isinstance(game_date, date):
                return {**data, "season_start_year": season_start_year_for(game_date)}
        return data

    @model_validator(mode="after")
    def _check_consistency(self) -> NormalizedGameLog:
        if self.team == self.opponent:
            raise ValueError("team and opponent must differ")
        return self

    @property
    def home_team(self) -> str:
        return self.team if self.is_home else self.opponent

    @property
    def away_team(self) -> str:
        return self.opponent if self.is_home else self.team

    @property
    def dedupe_key(self) -> tuple[date, str, str, str]:
        """Key identifying a unique (game, player) pair within a batch."""
        return (self.game_date, self.home_team, self.away_team, self.source_player_id)


class GameLogRow(BaseModel):
    """Read model returned by the game-log query service."""

    model_config = ConfigDict(frozen=True)

    game_date: date
    season: str
    player_id: int
    player_name: str
    team: str
    opponent: str
    is_home: bool
    minutes: float
    points: int
    rebounds: int
    assists: int
    steals: int | None = None
    blocks: int | None = None
    turnovers: int | None = None
    three_pointers_made: int | None = None
    started: bool | None = None
