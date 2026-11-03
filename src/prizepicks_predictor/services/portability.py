"""Backup, restore and snapshot export/import for moving an installation between machines.

* ``backup_database`` uses SQLite's online backup API (consistent even while open).
* ``restore_database`` verifies the source's integrity, refuses to overwrite a populated
  target unless forced, copies via the backup API, then re-runs ``init_db`` so triggers exist.
* ``export_snapshots`` / ``import_snapshots`` move prospective prediction snapshots (and their
  outcomes) as a small gzip CSV keyed by provider ids, so they survive a rebuild of the game
  logs from public data on another machine. Snapshots that were never saved cannot be
  recreated after the fact; this is the only way to carry them.
"""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from prizepicks_predictor.db.models import (
    ExternalId,
    Player,
    PredictionOutcome,
    PredictionSnapshot,
    ScheduledGame,
)
from prizepicks_predictor.db.session import init_db

SNAPSHOT_FIELDS = [
    "batch_id",
    "dedupe_key",
    "revision",
    "team",
    "opponent",
    "is_home",
    "participant_status",
    "stat_type",
    "target_date",
    "cutoff_at",
    "generated_at",
    "tipoff_utc",
    "model_name",
    "model_version",
    "distribution_family",
    "calibration_artifact",
    "calibration_status",
    "projection",
    "dist_mean",
    "dist_std",
    "q_lo",
    "q_hi",
    "line",
    "line_source",
    "p_more",
    "p_less",
    "p_push",
    "p_more_cal",
    "p_less_cal",
    "p_push_cal",
    "sample_size",
    "season_games",
    "history_latest_game_date",
    "data_source",
    "features_json",
    "warnings_json",
]
OUTCOME_FIELDS = [
    "evaluated_at",
    "status",
    "actual",
    "actual_minutes",
    "abs_error",
    "sq_error",
    "covered",
    "outcome_more",
    "outcome_push",
    "brier_raw",
    "brier_cal",
    "logloss_raw",
    "logloss_cal",
    "notes",
]
EXTRA = [
    "player_external_id",
    "player_external_source",
    "player_name",
    "scheduled_game_source",
    "scheduled_game_external_id",
]


class PortabilityError(RuntimeError):
    pass


def sqlite_path(engine: Engine) -> Path:
    url = make_url(str(engine.url))
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise PortabilityError("backup/restore support file-based SQLite databases only")
    return Path(url.database)


def backup_database(engine: Engine, dest: Path) -> Path:
    src_path = sqlite_path(engine)
    if not src_path.is_file():
        raise PortabilityError(f"database file not found: {src_path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with sqlite3.connect(src_path) as src, sqlite3.connect(tmp) as out:
        src.backup(out)
    with sqlite3.connect(tmp) as out:
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            tmp.unlink(missing_ok=True)
            raise PortabilityError("backup failed its integrity check")
    tmp.replace(dest)
    return dest


def restore_database(engine: Engine, source: Path, *, force: bool = False) -> Path:
    if not source.is_file():
        raise PortabilityError(f"backup file not found: {source}")
    with sqlite3.connect(source) as src:
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise PortabilityError(f"{source} fails PRAGMA integrity_check; not restoring")
        tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "player_game_stats" not in tables:
        raise PortabilityError(f"{source} is not a PrizePicks Predictor database")
    target = sqlite_path(engine)
    if target.is_file() and target.stat().st_size > 0 and not force:
        with sqlite3.connect(target) as cur:
            try:
                rows = cur.execute("SELECT count(*) FROM player_game_stats").fetchone()[0]
            except sqlite3.OperationalError:
                rows = 0
        if rows:
            raise PortabilityError(
                f"target {target} already holds {rows} player-game rows; "
                "re-run with --force to replace it "
                "(a safety backup is written first)"
            )
    engine.dispose()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0:
        safety = target.with_name(
            f"{target.stem}.pre-restore-{datetime.now():%Y%m%d%H%M%S}{target.suffix}"
        )
        with sqlite3.connect(target) as cur, sqlite3.connect(safety) as out:
            cur.backup(out)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as out:
        src.backup(out)
    init_db(engine)  # adds any newer tables/columns/triggers
    return target


@dataclass
class SnapshotTransfer:
    exported: int = 0
    imported: int = 0
    skipped_existing: int = 0
    skipped_unknown_player: int = 0
    unlinked_schedule: int = 0
    outcomes_imported: int = 0
    players_created: int = 0


def export_snapshots(session: Session, dest: Path) -> SnapshotTransfer:
    ext = {
        pid: (src, xid)
        for pid, src, xid in session.execute(
            select(ExternalId.entity_id, ExternalId.source, ExternalId.external_id).where(
                ExternalId.entity_type == "player"
            )
        )
    }
    stmt = (
        select(PredictionSnapshot, Player, PredictionOutcome, ScheduledGame)
        .join(Player, PredictionSnapshot.player_id == Player.id)
        .outerjoin(PredictionOutcome, PredictionOutcome.snapshot_id == PredictionSnapshot.id)
        .outerjoin(ScheduledGame, PredictionSnapshot.scheduled_game_id == ScheduledGame.id)
        .order_by(PredictionSnapshot.id)
    )
    result = SnapshotTransfer()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(dest, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=EXTRA + SNAPSHOT_FIELDS + [f"outcome_{f}" for f in OUTCOME_FIELDS]
        )
        writer.writeheader()
        for snap, player, outcome, sg in session.execute(stmt):
            src, xid = ext.get(snap.player_id, ("", ""))
            row: dict[str, object] = {
                "player_external_id": xid,
                "player_external_source": src,
                "player_name": player.full_name,
                "scheduled_game_source": sg.source if sg else "",
                "scheduled_game_external_id": sg.external_id if sg else "",
            }
            for f in SNAPSHOT_FIELDS:
                row[f] = _cell(getattr(snap, f))
            for f in OUTCOME_FIELDS:
                row[f"outcome_{f}"] = _cell(getattr(outcome, f)) if outcome else ""
            writer.writerow(row)
            result.exported += 1
    return result


def _cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _parse(field: str, raw: str) -> object:
    if raw == "":
        return None
    if field in ("is_home", "covered", "outcome_more", "outcome_push"):
        return bool(int(raw))
    if field in ("revision", "sample_size", "season_games", "actual"):
        return int(raw)
    if field in ("target_date", "history_latest_game_date"):
        return date.fromisoformat(raw)
    if field in ("cutoff_at", "generated_at", "tipoff_utc", "evaluated_at"):
        return datetime.fromisoformat(raw)
    if field in (
        "projection",
        "dist_mean",
        "dist_std",
        "q_lo",
        "q_hi",
        "line",
        "p_more",
        "p_less",
        "p_push",
        "p_more_cal",
        "p_less_cal",
        "p_push_cal",
        "actual_minutes",
        "abs_error",
        "sq_error",
        "brier_raw",
        "brier_cal",
        "logloss_raw",
        "logloss_cal",
    ):
        return float(raw)
    return raw


def import_snapshots(
    session: Session, source: Path, *, create_missing_players: bool = False
) -> SnapshotTransfer:
    """Insert exported snapshots whose dedupe_key is not already present. Never updates.

    ``create_missing_players`` adds a player identity row (name + provider id) for
    snapshots whose player is absent, e.g. on a database without that season's box scores.
    It never creates game logs.
    """
    result = SnapshotTransfer()
    players = {
        (src, xid): pid
        for pid, src, xid in session.execute(
            select(ExternalId.entity_id, ExternalId.source, ExternalId.external_id).where(
                ExternalId.entity_type == "player"
            )
        )
    }
    games = {(g.source, g.external_id): g.id for g in session.scalars(select(ScheduledGame))}
    existing = set(session.scalars(select(PredictionSnapshot.dedupe_key)))
    with gzip.open(source, "rt", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["dedupe_key"] in existing:
                result.skipped_existing += 1
                continue
            pid = players.get((row["player_external_source"], row["player_external_id"]))
            if pid is None and create_missing_players and row["player_external_id"]:
                player = Player(full_name=row["player_name"])
                session.add(player)
                session.flush()
                session.add(
                    ExternalId(
                        source=row["player_external_source"],
                        entity_type="player",
                        entity_id=player.id,
                        external_id=row["player_external_id"],
                    )
                )
                players[(row["player_external_source"], row["player_external_id"])] = player.id
                pid = player.id
                result.players_created += 1
            if pid is None:
                result.skipped_unknown_player += 1
                continue
            sg_id = games.get((row["scheduled_game_source"], row["scheduled_game_external_id"]))
            if sg_id is None and row["scheduled_game_external_id"]:
                result.unlinked_schedule += 1
            snap = PredictionSnapshot(
                player_id=pid,
                scheduled_game_id=sg_id,
                **{f: _parse(f, row[f]) for f in SNAPSHOT_FIELDS},
            )
            session.add(snap)
            session.flush()
            if row.get("outcome_status"):
                session.add(
                    PredictionOutcome(
                        snapshot_id=snap.id,
                        **{f: _parse(f, row[f"outcome_{f}"]) for f in OUTCOME_FIELDS},
                    )
                )
                result.outcomes_imported += 1
            existing.add(row["dedupe_key"])
            result.imported += 1
    session.commit()
    return result


def snapshot_counts(session: Session) -> tuple[int, int]:
    return (
        int(session.scalar(select(func.count()).select_from(PredictionSnapshot)) or 0),
        int(session.scalar(select(func.count()).select_from(PredictionOutcome)) or 0),
    )


def describe_json(path: Path) -> str:
    return json.dumps({"path": str(path), "bytes": path.stat().st_size})
