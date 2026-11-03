from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db import ScheduledGame, Team
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction.schemas import StatType
from prizepicks_predictor.services.forecast import ManualTarget, run_forecast

TARGET = date(2024, 12, 1)


@pytest.fixture
def loaded(session: Session, synthetic_csv: Path) -> Session:
    load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    return session


@pytest.fixture
def with_forecasts(loaded: Session) -> Session:
    bos = loaded.scalar(select(Team).where(Team.abbreviation == "BOS"))
    lal = loaded.scalar(select(Team).where(Team.abbreviation == "LAL"))
    assert bos and lal
    loaded.add(
        ScheduledGame(
            source="test",
            external_id="g1",
            season_start_year=2024,
            game_date=TARGET,
            tipoff_utc=datetime(2024, 12, 2, 0, 0),
            home_team_id=bos.id,
            away_team_id=lal.id,
            status="scheduled",
            loaded_at=datetime(2024, 11, 30),
        )
    )
    loaded.commit()
    run_forecast(
        loaded,
        target_date=TARGET,
        stats=list(StatType),
        now=datetime(2024, 12, 1, 18, tzinfo=UTC),
        save=True,
    )
    run_forecast(
        loaded,
        target_date=date(2024, 11, 20),
        stats=[StatType.POINTS],
        now=datetime(2024, 11, 20, 12, tzinfo=UTC),
        save=True,
        manual_targets=[ManualTarget(1, StatType.POINTS, 20.5)],
    )
    return loaded
