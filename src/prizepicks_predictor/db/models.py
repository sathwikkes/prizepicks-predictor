"""SQLAlchemy ORM models for the Phase 1 schema.

Design notes
------------
* Every entity has an integer surrogate primary key. Provider identifiers are
  stored in ``external_ids`` so a player/team/game can be matched across
  several data sources without baking any one provider's IDs into the core
  tables.
* Natural uniqueness constraints protect against duplicate records:
  - ``teams.abbreviation``
  - ``games (game_date, home_team_id, away_team_id)``; ``games.season_type`` separates
    regular-season games from playoffs/play-in/preseason
  - ``player_game_stats (game_id, player_id)``
  - ``external_ids (source, entity_type, external_id)``
* ``ingestion_runs`` records provenance for every load. Each stat row points at
  the run that last wrote it.
* Minutes are stored as a float of decimal minutes (34:30 -> 34.5) so they can
  be used directly in per-minute rate features later.

Phase 3.5 adds ``scheduled_games`` (upcoming games from a schedule source, mutable
facts), ``prediction_snapshots`` (immutable pregame forecasts; updates are refused at the
ORM level) and ``prediction_outcomes`` (one evaluation row per snapshot, written after
the game). Still planned: projections (real lines), model_versions.
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
    event,
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
    #: regular | playoffs | playin | preseason | allstar | other
    season_type: Mapped[str] = mapped_column(
        String(16), default="regular", server_default="regular", nullable=False, index=True
    )

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


class ScheduledGame(Base):
    """A game as published by a schedule source. Status and scores are updated on reload."""

    __tablename__ = "scheduled_games"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_scheduled_game_source_id"),
        Index("ix_scheduled_games_date", "game_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    season_start_year: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type: Mapped[str] = mapped_column(String(16), default="regular", nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)  # local (US) game date
    tipoff_utc: Mapped[datetime | None] = mapped_column(DateTime)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    #: scheduled | in_progress | final | postponed | canceled | unknown
    status: Mapped[str] = mapped_column(String(16), default="scheduled", nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class PredictionSnapshot(Base):
    """An immutable pregame forecast. Nothing here changes after insert."""

    __tablename__ = "prediction_snapshots"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_prediction_snapshot_dedupe"),
        Index("ix_prediction_snapshots_target", "target_date", "stat_type"),
        Index("ix_prediction_snapshots_player", "player_id", "target_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: player:target_date:stat:model:line:revision — identical forecasts are not re-inserted
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    scheduled_game_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_games.id"))
    team: Mapped[str | None] = mapped_column(String(5))
    opponent: Mapped[str | None] = mapped_column(String(5))
    is_home: Mapped[bool | None] = mapped_column(Boolean)
    #: expected (inferred from recent participation) | confirmed (verified pregame source)
    participant_status: Mapped[str] = mapped_column(String(16), default="expected", nullable=False)

    stat_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # UTC
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # UTC
    tipoff_utc: Mapped[datetime | None] = mapped_column(DateTime)

    model_name: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str] = mapped_column(String(16), nullable=False)
    distribution_family: Mapped[str] = mapped_column(String(24), nullable=False)
    calibration_artifact: Mapped[str | None] = mapped_column(String(160))
    calibration_status: Mapped[str] = mapped_column(String(160), nullable=False)

    projection: Mapped[float] = mapped_column(Float, nullable=False)
    dist_mean: Mapped[float] = mapped_column(Float, nullable=False)
    dist_std: Mapped[float] = mapped_column(Float, nullable=False)
    q_lo: Mapped[float] = mapped_column(Float, nullable=False)
    q_hi: Mapped[float] = mapped_column(Float, nullable=False)

    line: Mapped[float | None] = mapped_column(Float)
    #: none | synthetic_trailing_median | manual
    line_source: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    p_more: Mapped[float | None] = mapped_column(Float)
    p_less: Mapped[float | None] = mapped_column(Float)
    p_push: Mapped[float | None] = mapped_column(Float)
    p_more_cal: Mapped[float | None] = mapped_column(Float)
    p_less_cal: Mapped[float | None] = mapped_column(Float)
    p_push_cal: Mapped[float | None] = mapped_column(Float)

    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    season_games: Mapped[int] = mapped_column(Integer, nullable=False)
    history_latest_game_date: Mapped[date | None] = mapped_column(Date)
    data_source: Mapped[str] = mapped_column(String(64), nullable=False)
    features_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False)

    player: Mapped[Player] = relationship()
    scheduled_game: Mapped[ScheduledGame | None] = relationship()
    outcome: Mapped[PredictionOutcome | None] = relationship(
        back_populates="snapshot", uselist=False
    )


class SnapshotImmutableError(RuntimeError):
    pass


@event.listens_for(PredictionSnapshot, "before_update")
def _refuse_snapshot_update(
    _mapper: object, _connection: object, target: PredictionSnapshot
) -> None:
    raise SnapshotImmutableError(f"prediction snapshot {target.id} is immutable")


class PredictionOutcome(Base):
    """Post-game evaluation of one snapshot. Written once; the snapshot is never touched."""

    __tablename__ = "prediction_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("prediction_snapshots.id"), unique=True, nullable=False
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # UTC
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"))
    #: evaluated | did_not_play | missing_result | postponed | voided
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    actual: Mapped[int | None] = mapped_column(Integer)
    actual_minutes: Mapped[float | None] = mapped_column(Float)
    abs_error: Mapped[float | None] = mapped_column(Float)
    sq_error: Mapped[float | None] = mapped_column(Float)
    covered: Mapped[bool | None] = mapped_column(Boolean)
    outcome_more: Mapped[bool | None] = mapped_column(Boolean)
    outcome_push: Mapped[bool | None] = mapped_column(Boolean)
    brier_raw: Mapped[float | None] = mapped_column(Float)
    brier_cal: Mapped[float | None] = mapped_column(Float)
    logloss_raw: Mapped[float | None] = mapped_column(Float)
    logloss_cal: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)

    snapshot: Mapped[PredictionSnapshot] = relationship(back_populates="outcome")


class TeamGameStat(Base):
    """One team's box-score totals for one game (pace and defence inputs)."""

    __tablename__ = "team_game_stats"
    __table_args__ = (UniqueConstraint("game_id", "team_id", name="uq_team_game"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    minutes: Mapped[float] = mapped_column(Float, nullable=False)  # team minutes (240 regulation)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    fga: Mapped[int] = mapped_column(Integer, nullable=False)
    fta: Mapped[int] = mapped_column(Integer, nullable=False)
    oreb: Mapped[int] = mapped_column(Integer, nullable=False)
    dreb: Mapped[int] = mapped_column(Integer, nullable=False)
    tov: Mapped[int] = mapped_column(Integer, nullable=False)
    ast: Mapped[int] = mapped_column(Integer, nullable=False)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"))

    game: Mapped[Game] = relationship()
    team: Mapped[Team] = relationship()
