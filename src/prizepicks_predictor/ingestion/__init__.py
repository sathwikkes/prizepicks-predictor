"""Data ingestion: provider adapters + loader that persists normalized records."""

from prizepicks_predictor.ingestion.base import (
    GameLogProvider,
    ProviderError,
    RecordValidationError,
)
from prizepicks_predictor.ingestion.csv_provider import CsvGameLogProvider
from prizepicks_predictor.ingestion.loader import LoadResult, load_game_logs

__all__ = [
    "CsvGameLogProvider",
    "GameLogProvider",
    "LoadResult",
    "ProviderError",
    "RecordValidationError",
    "load_game_logs",
]
