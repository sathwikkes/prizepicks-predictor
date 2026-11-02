"""Provider adapter contract.

A provider turns some external representation (CSV rows, JSON from an HTTP
API, ...) into an iterable of ``NormalizedGameLog`` records. Adapters are
interchangeable: the loader and everything downstream depend only on this
protocol.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from prizepicks_predictor.schemas.game_log import NormalizedGameLog


class ProviderError(Exception):
    """Raised when a provider cannot deliver data (network, auth, malformed payload)."""


class RecordValidationError(ValueError):
    """Raised for a single malformed record. Carries enough context to log it."""

    def __init__(self, message: str, *, record_ref: str | None = None) -> None:
        super().__init__(message)
        self.record_ref = record_ref


@runtime_checkable
class GameLogProvider(Protocol):
    """Source of player game logs."""

    #: Short, stable identifier stored in ``ingestion_runs.source`` and ``external_ids.source``.
    source_name: str

    def describe(self) -> str:
        """Human-readable description of what will be fetched (file path, query, ...)."""
        ...

    def iter_game_logs(self) -> Iterator[NormalizedGameLog | RecordValidationError]:
        """Yield validated records, or a ``RecordValidationError`` for each bad record.

        Yielding errors instead of raising lets the loader keep going and report
        rejected rows in the ingestion run, while a ``ProviderError`` (raised, not
        yielded) aborts the whole run.
        """
        ...
