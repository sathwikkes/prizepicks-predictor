"""Pydantic schemas shared between ingestion, services, and (later) the API."""

from prizepicks_predictor.schemas.game_log import GameLogRow, NormalizedGameLog

__all__ = ["GameLogRow", "NormalizedGameLog"]
