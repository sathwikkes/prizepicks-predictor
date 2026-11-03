"""Shared Streamlit helpers: cached engine, session access, disclaimers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import streamlit as st
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from prizepicks_predictor.config import get_settings
from prizepicks_predictor.db import create_db_engine, get_session_factory, init_db
from prizepicks_predictor.prediction.service import resolve_calibration_dir

RESEARCH_NOTE = (
    "Research tool. Probabilities are calibrated against synthetic thresholds built from each "
    "player's own history, not against PrizePicks or sportsbook lines. Nothing here is a wagering "
    "recommendation. Underlying statistics are NBA.com data republished by SportsDataverse and "
    "used non-commercially; see docs/LICENSING_RISK.md."
)


@st.cache_resource(show_spinner=False)
def _factory(database_url: str) -> sessionmaker[Session]:
    engine = create_db_engine(database_url)
    init_db(engine)
    return get_session_factory(engine)


@contextmanager
def db_session() -> Iterator[Session]:
    with _factory(get_settings().database_url)() as session:
        yield session


def calibration_dir() -> Path:
    return resolve_calibration_dir(get_settings().calibration_dir)


def guarded(page: Callable[[], None]) -> Callable[[], None]:
    """Turn database failures into a readable error state instead of a traceback."""

    @wraps(page)
    def wrapper() -> None:
        try:
            page()
        except (OperationalError, OSError) as exc:
            detail = getattr(exc, "orig", exc)
            st.error(
                "Database unavailable. Check `PPP_DATABASE_URL` and that the file exists and "
                f"is readable.\n\n`{detail}`"
            )
            st.caption(f"Configured URL: {get_settings().database_url}")
        except SQLAlchemyError as exc:
            st.error(f"Database error: {exc.__class__.__name__}: {exc}")

    return wrapper


def backtest_dirs() -> list[Path]:
    """Committed summaries first, then any local full runs."""
    return [Path("docs/backtests"), Path("data/backtests")]


def footer() -> None:
    st.caption(RESEARCH_NOTE)
