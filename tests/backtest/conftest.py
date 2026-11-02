from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from prizepicks_predictor.backtest.config import BacktestConfig
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction.schemas import StatType


@pytest.fixture
def loaded(session: Session, synthetic_csv: Path) -> Session:
    """Synthetic fixture: 6 fictional players x 12 games in 2024-25 (software check only)."""
    load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    return session


@pytest.fixture
def small_config() -> BacktestConfig:
    return BacktestConfig(seasons=[2024], stats=[StatType.POINTS], min_prior_games=3)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'cli.db'}"
