"""Engine and session helpers.

SQLite needs two tweaks for correct behaviour:
* ``PRAGMA foreign_keys=ON`` so FK constraints are enforced.
* A ``check_same_thread=False`` connect arg so the engine works under FastAPI later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from prizepicks_predictor.db.models import Base


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    url = make_url(database_url)
    connect_args: dict[str, Any] = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, echo=echo, connect_args=connect_args)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


#: Columns added after the first release, applied to pre-existing databases.
#: Alembic replaces this once the schema needs anything more than ADD COLUMN.
_COMPAT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("games", "season_type", "VARCHAR(16) NOT NULL DEFAULT 'regular'"),
)


def init_db(engine: Engine) -> None:
    """Create all tables and add any columns missing from an older database. Idempotent."""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    for table, column, ddl in _COMPAT_COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
