from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from prizepicks_predictor.db import ScheduledGame, Team
from prizepicks_predictor.ingestion import CsvGameLogProvider, load_game_logs
from prizepicks_predictor.prediction.calibrators import CalibrationArtifact, CalibratorRegistry
from prizepicks_predictor.prediction.schemas import DistributionFamily, StatType

#: Synthetic fixture: six fictional players, games 2024-10-26 .. 2024-11-27 (2024-25 season).
TARGET = date(2024, 12, 1)
TIPOFF = datetime(2024, 12, 2, 0, 0)  # 19:00 ET on 2024-12-01, stored naive UTC
BEFORE_TIPOFF = datetime(2024, 12, 1, 18, 0, tzinfo=UTC)
AFTER_TIPOFF = datetime(2024, 12, 2, 3, 0, tzinfo=UTC)


@pytest.fixture
def loaded(session: Session, synthetic_csv: Path) -> Session:
    load_game_logs(session, CsvGameLogProvider(synthetic_csv, source_name="csv_fixture"))
    return session


def _team(session: Session, abbr: str) -> Team:
    t = session.scalar(select(Team).where(Team.abbreviation == abbr))
    if t is None:
        t = Team(abbreviation=abbr)
        session.add(t)
        session.flush()
    return t


@pytest.fixture
def scheduled(loaded: Session) -> Session:
    """Two scheduled games on TARGET: BOS vs LAL (tip 00:00Z next day), DEN @ MIA (no tip time)."""
    bos, lal, den, mia = (_team(loaded, a) for a in ("BOS", "LAL", "DEN", "MIA"))
    loaded.add_all(
        [
            ScheduledGame(
                source="test_schedule",
                external_id="g1",
                season_start_year=2024,
                game_date=TARGET,
                tipoff_utc=TIPOFF,
                home_team_id=bos.id,
                away_team_id=lal.id,
                status="scheduled",
                loaded_at=datetime(2024, 11, 30),
            ),
            ScheduledGame(
                source="test_schedule",
                external_id="g2",
                season_start_year=2024,
                game_date=TARGET,
                tipoff_utc=None,
                home_team_id=mia.id,
                away_team_id=den.id,
                status="scheduled",
                loaded_at=datetime(2024, 11, 30),
            ),
        ]
    )
    loaded.commit()
    return loaded


def make_artifact(
    stat: StatType = StatType.POINTS, *, model_version: str = "1.0"
) -> CalibrationArtifact:
    """A shrink-toward-0.5 isotonic map, deliberately not the identity."""
    return CalibrationArtifact(
        artifact_id=f"test.{stat.value}",
        stat=stat,
        model_name="ewma",
        model_version=model_version,
        family=DistributionFamily.AUTO,
        method="isotonic",
        x=[0.2, 0.4, 0.6, 0.8, 1.0],
        y=[0.3, 0.42, 0.55, 0.68, 0.8],
        n_fit=5000,
        fit_seasons=[2024],
        fit_runs=["test"],
        data_source="test",
        data_latest_game_date=date(2025, 4, 1),
        fitted_at=datetime(2025, 5, 1, tzinfo=UTC),
        validation_status="in_sample_only",
    )


@pytest.fixture
def registry() -> CalibratorRegistry:
    return CalibratorRegistry(
        [
            make_artifact(StatType.POINTS),
            make_artifact(StatType.REBOUNDS),
            make_artifact(StatType.ASSISTS),
        ]
    )


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'cli.db'}"
