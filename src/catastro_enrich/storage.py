"""Reference SQLite adapter. The library itself only receives and returns
records; this module shows how a store plugs in: load records, apply diffs,
keep image metadata and take a timestamped backup before writing."""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import EnrichmentDiff, ImageDocument, PropertyRecord

PROPERTIES_TABLE = "properties"
IMAGES_TABLE = "property_images"

WRITABLE_FIELDS = (
    "lat",
    "lon",
    "street",
    "built_area_m2",
    "construction_year",
    "cadastral_use",
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {PROPERTIES_TABLE} (
    id TEXT PRIMARY KEY,
    inventory_number TEXT DEFAULT '',
    name TEXT DEFAULT '',
    cadastral_reference TEXT,
    street TEXT,
    locality TEXT,
    lat REAL,
    lon REAL,
    built_area_m2 REAL,
    construction_year INTEGER,
    asset_type TEXT,
    status TEXT,
    cadastral_use TEXT,
    municipality_id TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS {IMAGES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    mime TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    attribution TEXT DEFAULT '',
    captured_at TEXT,
    UNIQUE (property_id, source, kind)
);
"""


class BackupError(RuntimeError):
    """Raised when the pre-write snapshot cannot be taken."""


class SqliteInventory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    # --- records --------------------------------------------------------------

    def insert(self, records: Iterable[PropertyRecord]) -> int:
        rows = [r.to_dict() for r in records]
        if not rows:
            return 0
        columns = list(rows[0])
        sql = (
            f"INSERT OR REPLACE INTO {PROPERTIES_TABLE} ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
        with self._connect() as con:
            con.executemany(sql, [[row[c] for c in columns] for row in rows])
        return len(rows)

    def load(self, municipality_id: str | None = None) -> list[PropertyRecord]:
        sql = f"SELECT * FROM {PROPERTIES_TABLE}"
        args: list[Any] = []
        if municipality_id:
            sql += " WHERE municipality_id = ?"
            args.append(municipality_id)
        sql += " ORDER BY inventory_number, id"
        with self._connect() as con:
            rows = con.execute(sql, args).fetchall()
        return [PropertyRecord.from_dict(dict(row)) for row in rows]

    def get(self, property_id: str) -> PropertyRecord | None:
        with self._connect() as con:
            row = con.execute(
                f"SELECT * FROM {PROPERTIES_TABLE} WHERE id = ?", (property_id,)
            ).fetchone()
        return PropertyRecord.from_dict(dict(row)) if row else None

    def apply(self, record: PropertyRecord, diff: EnrichmentDiff) -> int:
        """Persist the diff's writable fields; return how many were set."""
        fields = {k: v for k, v in diff.changes.items() if k in WRITABLE_FIELDS}
        if not fields:
            return 0
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._connect() as con:
            con.execute(
                f"UPDATE {PROPERTIES_TABLE} SET {assignments} WHERE id = ?",
                [*fields.values(), record.id],
            )
        return len(fields)

    # --- images ---------------------------------------------------------------

    def images(self, property_id: str | None = None) -> list[ImageDocument]:
        sql = f"SELECT * FROM {IMAGES_TABLE}"
        args: list[Any] = []
        if property_id:
            sql += " WHERE property_id = ?"
            args.append(property_id)
        with self._connect() as con:
            rows = con.execute(sql, args).fetchall()
        return [
            ImageDocument(
                property_id=row["property_id"],
                filename=row["filename"],
                relative_path=row["relative_path"],
                mime=row["mime"],
                sha256=row["sha256"],
                size=row["size"],
                source=row["source"],
                kind=row["kind"],
                attribution=row["attribution"] or "",
                captured_at=row["captured_at"],
            )
            for row in rows
        ]

    def add_image(self, doc: ImageDocument) -> None:
        with self._connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO {IMAGES_TABLE} (property_id, filename, relative_path,"
                " mime, sha256, size, source, kind, attribution, captured_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc.property_id,
                    doc.filename,
                    doc.relative_path,
                    doc.mime,
                    doc.sha256,
                    doc.size,
                    doc.source,
                    doc.kind,
                    doc.attribution,
                    doc.captured_at,
                ),
            )

    # --- backup ---------------------------------------------------------------

    def backup(self, directory: str | Path | None = None, *, now: datetime | None = None) -> Path:
        """Consistent timestamped snapshot taken with the SQLite backup API.
        Raises :class:`BackupError` when it cannot be written."""
        stamp = (now or datetime.now(tz=UTC)).strftime("%Y%m%dT%H%M%SZ")
        target_dir = Path(directory) if directory else self.path.parent
        target = target_dir / f"{self.path.stem}.backup-{stamp}{self.path.suffix}"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise BackupError(f"backup target already exists: {target}")
            with sqlite3.connect(self.path) as src, sqlite3.connect(target) as dst:
                src.backup(dst)
        except (OSError, sqlite3.Error) as exc:
            raise BackupError(f"cannot back up {self.path}: {exc}") from exc
        if shutil.disk_usage(target_dir).free <= 0:
            raise BackupError("no free space after backup")
        return target
