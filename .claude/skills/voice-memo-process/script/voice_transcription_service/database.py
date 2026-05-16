"""SQLite database utilities for script."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional


ISO_FORMAT = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(ISO_FORMAT)


class Database:
    """Lightweight SQLite wrapper with helpers for audio transcription state."""

    def __init__(self, db_path: Path, timeout: float = 10.0):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    @contextmanager
    def connect(self) -> Iterable[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        """Create tables and indexes if they do not already exist."""

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS audio_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    status_updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    output_path TEXT,
                    output_filename TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    last_error TEXT,
                    synced_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audio_status ON audio_files(status)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audio_created ON audio_files(created_at)
                """
            )
            conn.commit()

    def ingest_file(self, file_path: Path) -> Dict[str, str]:
        """Ensure a file is registered in the database and return its record."""

        file_path = file_path.expanduser().resolve()
        stat = file_path.stat()
        filename = file_path.name
        created_at = datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime))
        modified_at = datetime.fromtimestamp(stat.st_mtime)

        with self.connect() as conn:
            cursor = conn.cursor()
            now = _now()
            cursor.execute(
                """
                INSERT INTO audio_files (
                    filename, file_path, file_size, created_at, modified_at,
                    status, status_updated_at, attempt_count, synced_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?)
                ON CONFLICT(filename) DO UPDATE SET
                    file_path=excluded.file_path,
                    file_size=excluded.file_size,
                    modified_at=excluded.modified_at,
                    synced_at=excluded.synced_at
                """,
                (
                    filename,
                    str(file_path),
                    stat.st_size,
                    created_at.strftime(ISO_FORMAT),
                    modified_at.strftime(ISO_FORMAT),
                    now,
                    now,
                ),
            )
            conn.commit()

        record = self.get_file_by_filename(filename)
        if record is None:
            raise RuntimeError(f"Failed to ingest file metadata for {file_path}")
        return record

    def get_file_by_filename(self, filename: str) -> Optional[Dict[str, str]]:
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audio_files WHERE filename = ?", (filename,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_file_paths(self) -> set:
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_path FROM audio_files")
            return {row[0] for row in cursor.fetchall()}

    def get_file_by_path(self, file_path: Path) -> Optional[Dict[str, str]]:
        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audio_files WHERE file_path = ?", (str(file_path),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def fetch_files(
        self,
        status: Optional[str] = None,
        days: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, str]]:
        """Fetch candidate records based on filters."""

        query = "SELECT * FROM audio_files WHERE 1=1"
        params: List[object] = []

        if status:
            query += " AND status = ?"
            params.append(status)

        if days is not None:
            time_limit = datetime.now() - timedelta(days=days)
            query += " AND datetime(created_at) >= datetime(?)"
            params.append(time_limit.strftime(ISO_FORMAT))

        query += " ORDER BY datetime(created_at) DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        if offset:
            query += " OFFSET ?"
            params.append(offset)

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def mark_processing(self, filename: str) -> None:
        """Mark a file as currently being processed."""

        with self.connect() as conn:
            cursor = conn.cursor()
            now = _now()
            cursor.execute(
                """
                UPDATE audio_files
                SET status = 'processing',
                    status_updated_at = ?,
                    attempt_count = attempt_count + 1,
                    last_attempt_at = ?,
                    last_error = NULL
                WHERE filename = ?
                """,
                (now, now, filename),
            )
            conn.commit()

    def mark_completed(self, filename: str, output_path: Path) -> None:
        """Mark a file as successfully processed."""

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE audio_files
                SET status = 'completed',
                    status_updated_at = ?,
                    output_path = ?,
                    output_filename = ?,
                    last_error = NULL
                WHERE filename = ?
                """,
                (
                    _now(),
                    str(output_path.parent),
                    output_path.name,
                    filename,
                ),
            )
            conn.commit()

    def mark_error(self, filename: str, error: str) -> None:
        """Mark a file as failed with an error message."""

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE audio_files
                SET status = 'error',
                    status_updated_at = ?,
                    last_error = ?
                WHERE filename = ?
                """,
                (_now(), error[:1000], filename),
            )
            conn.commit()
