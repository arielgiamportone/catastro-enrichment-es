"""Per-run telemetry. A telemetry failure never aborts a batch: the guarded
helpers swallow and log exceptions."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from .models import RunStats

log = logging.getLogger(__name__)

RUNS_TABLE = "batch_runs"


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def iso(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class RunRecord:
    id: int
    municipality_id: str
    kind: str
    driver: str
    started_at: str
    finished_at: str | None = None
    duration_s: float | None = None
    queried: int = 0
    ok: int = 0
    errors: int = 0
    written: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = True
    launched_by: str = ""

    @property
    def closed(self) -> bool:
        return self.finished_at is not None


class RunLog(Protocol):
    def open_run(
        self, *, municipality_id: str, kind: str, driver: str, dry_run: bool, launched_by: str
    ) -> int: ...

    def close_run(self, run_id: int, stats: RunStats) -> None: ...

    def list_runs(self, municipality_id: str | None = None, limit: int = 20) -> list[RunRecord]: ...


class MemoryRunLog:
    """In-memory log, handy for tests and one-off scripts."""

    def __init__(self, *, clock=utc_now) -> None:
        self.records: dict[int, RunRecord] = {}
        self._started: dict[int, datetime] = {}
        self._clock = clock

    def open_run(
        self, *, municipality_id: str, kind: str, driver: str, dry_run: bool, launched_by: str
    ) -> int:
        run_id = len(self.records) + 1
        now = self._clock()
        self.records[run_id] = RunRecord(
            id=run_id,
            municipality_id=municipality_id,
            kind=kind,
            driver=driver,
            started_at=iso(now),
            dry_run=dry_run,
            launched_by=launched_by,
        )
        self._started[run_id] = now
        return run_id

    def close_run(self, run_id: int, stats: RunStats) -> None:
        rec = self.records[run_id]
        now = self._clock()
        rec.finished_at = iso(now)
        rec.duration_s = max(0.0, (now - self._started[run_id]).total_seconds())
        rec.queried, rec.ok, rec.errors, rec.written = (
            stats.queried,
            stats.ok,
            stats.errors,
            stats.written,
        )
        rec.extra = stats.extra()

    def list_runs(self, municipality_id: str | None = None, limit: int = 20) -> list[RunRecord]:
        rows = [r for r in self.records.values() if municipality_id in (None, r.municipality_id)]
        rows.sort(key=lambda r: (r.started_at, r.id), reverse=True)
        return rows[:limit]


class SqliteRunLog:
    """Run log in a SQLite table (``batch_runs``), one row per batch."""

    def __init__(self, path: str | Path, *, clock=utc_now) -> None:
        self.path = Path(path)
        self._clock = clock
        self._started: dict[int, datetime] = {}
        with self._connect() as con:
            con.execute(
                f"""CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    municipality_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    driver TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_s REAL,
                    queried INTEGER DEFAULT 0,
                    ok INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0,
                    written INTEGER DEFAULT 0,
                    extra TEXT DEFAULT '{{}}',
                    dry_run INTEGER DEFAULT 1,
                    launched_by TEXT DEFAULT ''
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def open_run(
        self, *, municipality_id: str, kind: str, driver: str, dry_run: bool, launched_by: str
    ) -> int:
        now = self._clock()
        with self._connect() as con:
            cur = con.execute(
                f"INSERT INTO {RUNS_TABLE} (municipality_id, kind, driver, started_at,"
                " dry_run, launched_by) VALUES (?, ?, ?, ?, ?, ?)",
                (municipality_id, kind, driver, iso(now), int(dry_run), launched_by),
            )
            run_id = int(cur.lastrowid or 0)
        self._started[run_id] = now
        return run_id

    def close_run(self, run_id: int, stats: RunStats) -> None:
        now = self._clock()
        started = self._started.get(run_id)
        if started is None:
            with self._connect() as con:
                row = con.execute(
                    f"SELECT started_at FROM {RUNS_TABLE} WHERE id = ?", (run_id,)
                ).fetchone()
            started = (
                datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")) if row else now
            )
        duration = max(0.0, (now - started).total_seconds())
        with self._connect() as con:
            con.execute(
                f"UPDATE {RUNS_TABLE} SET finished_at = ?, duration_s = ?, queried = ?, ok = ?,"
                " errors = ?, written = ?, extra = ? WHERE id = ?",
                (
                    iso(now),
                    duration,
                    stats.queried,
                    stats.ok,
                    stats.errors,
                    stats.written,
                    json.dumps(stats.extra(), ensure_ascii=False),
                    run_id,
                ),
            )

    def list_runs(self, municipality_id: str | None = None, limit: int = 20) -> list[RunRecord]:
        sql = f"SELECT * FROM {RUNS_TABLE}"
        args: list[Any] = []
        if municipality_id is not None:
            sql += " WHERE municipality_id = ?"
            args.append(municipality_id)
        sql += " ORDER BY started_at DESC, id DESC LIMIT ?"
        args.append(int(limit))
        with self._connect() as con:
            rows = con.execute(sql, args).fetchall()
        return [
            RunRecord(
                id=row["id"],
                municipality_id=row["municipality_id"],
                kind=row["kind"],
                driver=row["driver"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                duration_s=row["duration_s"],
                queried=row["queried"],
                ok=row["ok"],
                errors=row["errors"],
                written=row["written"],
                extra=json.loads(row["extra"] or "{}"),
                dry_run=bool(row["dry_run"]),
                launched_by=row["launched_by"] or "",
            )
            for row in rows
        ]


def guarded_open(log_: RunLog | None, **kwargs: Any) -> int | None:
    if log_ is None:
        return None
    try:
        return log_.open_run(**kwargs)
    except Exception as exc:  # telemetry must never abort the batch
        log.warning("telemetry open failed: %s", exc)
        return None


def guarded_close(log_: RunLog | None, run_id: int | None, stats: RunStats) -> None:
    if log_ is None or run_id is None:
        return
    try:
        log_.close_run(run_id, stats)
    except Exception as exc:
        log.warning("telemetry close failed: %s", exc)
