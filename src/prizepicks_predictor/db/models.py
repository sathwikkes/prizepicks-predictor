"""SQLAlchemy ORM models for the Phase 1 schema.

Design notes
------------
* Every entity has an integer surrogate primary key. Provider identifiers are
  stored in ``external_ids`` so a player/team/game can be matched across
  several data sources without baking any one provider's IDs into the core
  tables.
* Natural uniqueness constraints protect against duplicate records:
  - ``teams.abbreviation``
  - ``games (game_date, home_team_id, away_team_id)``
  - ``player_game_stats (game_id, player_id)``
  - ``external_ids (source, entity_type, external_id)``
* ``ingestion_runs`` records provenance for every load. Each stat row points at
  the run that last wrote it.
* Minutes are stored as a float of decimal minutes (34:30 -> 34.5) so they can
  be used directly in per-minute rate features later.

Future tables (not implemented yet): projections, prediction_snapshots,
model_versions, prediction_outcomes.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Season(Base):
    """An NBA season identified by its starting calendar year (2024 -> '2024-25')."""

    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_year: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(9), nullable=False)

    games: Mapped[list[Game]] = relationship(back_populates="season")

    @staticmethod
    def label_for(start_year: int) -> str:
        return f"{start_year}-{(start_year + 1) % 100:02d}"


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    abbreviation: Mapped[str] = mapped_column(String(5), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(64))


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    position: Mapped[str | None] = mapped_column(String(8))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    game_stats: Mapped[list[PlayerGameStat]] = relationship(back_populates="player")


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("game_date", "home_team_id", "away_team_id", name="uq_game_date_teams"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="final", nullable=False)

    season: Mapped[Season] = relationship(back_populates="games")
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
    player_stats: Mapped[list[PlayerGameStat]] = relationship(back_populates="game")


class IngestionRun(Base):
    """Provenance record for a single ingestion execution."""

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    records_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_unchanged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class PlayerGameStat(Base):
    """One player's box-score line for one game."""

    __tablename__ = "player_game_stats"
    __table_args__ = (
        UniqueConstraint("game_id", "player_id", name="uq_player_game"),
        Index("ix_player_game_stats_player_game", "player_id", "game_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))

    minutes: Mapped[float] = mapped_column(Float, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    rebounds: Mapped[int] = mapped_column(Integer, nullable=False)
    assists: Mapped[int] = mapped_column(Integer, nullable=False)
    steals: Mapped[int | None] = mapped_column(Integer)
    blocks: Mapped[int | None] = mapped_column(Integer)
    turnovers: Mapped[int | None] = mapped_column(Integer)
    three_pointers_made: Mapped[int | None] = mapped_column(Integer)
    started: Mapped[bool | None] = mapped_column(Boolean)

    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    game: Mapped[Game] = relationship(back_populates="player_stats")
    player: Mapped[Player] = relationship(back_populates="game_stats")
    team: Mapped[Team] = relationship()

    #: Stat columns compared when deciding whether an incoming record changes a row.
    STAT_FIELDS: tuple[str, ...] = (
        "minutes",
        "points",
        "rebounds",
        "assists",
        "steals",
        "blocks",
        "turnovers",
        "three_pointers_made",
        "started",
    )


class ExternalId(Base):
    """Maps a provider's identifier onto an internal entity."""

    __tablename__ = "external_ids"
    __table_args__ = (
        UniqueConstraint("source", "entity_type", "external_id", name="uq_external_id"),
        Index("ix_external_ids_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # player|team|game
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
