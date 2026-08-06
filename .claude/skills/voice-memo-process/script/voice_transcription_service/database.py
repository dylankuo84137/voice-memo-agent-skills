"""SQLite database utilities for script."""

from __future__ import annotations

import os
import re
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .recording_time import recording_start_from_filename, resolve_recording_start


ISO_FORMAT = "%Y-%m-%d %H:%M:%S"

# How created_at appears inside an output filename: voice-memo_20260516_221502.md
OUTPUT_STAMP_FORMAT = "%Y%m%d_%H%M%S"
_OUTPUT_STAMP_RE = re.compile(r"(\d{8}_\d{6})")

# How long a row may sit in 'processing' before a later run assumes the run that
# claimed it died and hands the file back to 'pending'. Only applied once that
# run is known not to be running any more — see _owner_is_alive.
STALE_PROCESSING_MINUTES = 60

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS audio_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        file_path TEXT NOT NULL UNIQUE,
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
        synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
        processing_owner TEXT
    )
"""

_COLUMNS = (
    "filename, file_path, file_size, created_at, modified_at, status, "
    "status_updated_at, output_path, output_filename, attempt_count, "
    "last_attempt_at, last_error, synced_at"
)

# Rows stuck in 'processing' long enough to be considered abandoned.
_STALE_PREDICATE = (
    "status = 'processing' "
    "AND datetime(coalesce(last_attempt_at, status_updated_at)) <= datetime(?)"
)


def _now() -> str:
    return datetime.now().strftime(ISO_FORMAT)


def _owner_token() -> str:
    """Identify the run claiming a row: this host and this process."""

    return f"{socket.gethostname()}:{os.getpid()}"


def _owner_is_alive(token: Optional[str]) -> bool:
    """True when the run that claimed a row is still running on this machine.

    The memo directory is a FUSE mount, so the read inside transcribe() can
    block with no timeout: a row can sit in 'processing' past the stale window
    while its run is very much alive. Reclaiming it then makes a second run pay
    for the same audio and write over the same transcript. A live pid is the
    signal that the row is not abandoned; anything we cannot check (no owner
    recorded, a row claimed on another host) falls back to the time rule.

    pid reuse would make a dead run look alive and strand the row for good, but
    /proc/sys/kernel/pid_max is in the millions here, so recycling a pid inside
    the stale window is not a case worth a start-time comparison.
    """

    if not token:
        return False
    host, _, pid_text = token.rpartition(":")
    if host != socket.gethostname():
        return False
    try:
        pid = int(pid_text)
    except ValueError:
        return False
    if pid <= 0:  # signalling 0 would hit this whole process group
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # alive, just owned by another user
        return True
    except OSError:
        return False
    return True


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
            cursor.execute(_CREATE_TABLE)
            self._rekey_on_file_path(conn)
            self._add_processing_owner(conn)
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

    @staticmethod
    def _rekey_on_file_path(conn: sqlite3.Connection) -> None:
        """Migrate legacy databases from UNIQUE(filename) to UNIQUE(file_path).

        Keying on the bare filename made two same-named memos in different
        folders collide, silently overwriting the first one's path. Runs before
        the indexes are created so their names are free when the old table (and
        its indexes) is dropped.

        The rewrite is wrapped in one explicit transaction: sqlite3 auto-commits
        bare DDL, so without it a crash between the CREATE and the row copy would
        leave an empty new table that the check below reads as 'already
        migrated', orphaning every record.
        """

        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='audio_files'"
        ).fetchone()
        if row is None or "filename TEXT NOT NULL UNIQUE" not in row[0]:
            return

        cursor.execute("BEGIN IMMEDIATE")
        try:
            cursor.execute("ALTER TABLE audio_files RENAME TO audio_files_legacy")
            cursor.execute(_CREATE_TABLE)
            cursor.execute(
                f"INSERT INTO audio_files ({_COLUMNS}) "
                f"SELECT {_COLUMNS} FROM audio_files_legacy GROUP BY file_path"
            )
            cursor.execute("DROP TABLE audio_files_legacy")
        except Exception:
            conn.rollback()
            raise
        cursor.execute("COMMIT")

    @staticmethod
    def _add_processing_owner(conn: sqlite3.Connection) -> None:
        """Add the processing_owner column to a database created without it.

        A single ADD COLUMN, not the table rewrite above: existing rows want
        exactly what the new column defaults to (NULL, "owner unknown"), which
        the reclaim reads as the old time-only behaviour.
        """

        cursor = conn.cursor()
        columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(audio_files)").fetchall()
        }
        if "processing_owner" in columns:
            return
        cursor.execute("ALTER TABLE audio_files ADD COLUMN processing_owner TEXT")

    def ingest_file(self, file_path: Path) -> Dict[str, str]:
        """Ensure a file is registered in the database and return its record."""

        file_path = file_path.expanduser().resolve()
        stat = file_path.stat()
        filename = file_path.name
        modified_at = datetime.fromtimestamp(stat.st_mtime)
        created_at = resolve_recording_start(file_path, modified_at)

        with self.connect() as conn:
            cursor = conn.cursor()
            now = _now()
            self._adopt_relocated_row(
                cursor, filename, stat.st_size, modified_at, file_path
            )
            cursor.execute(
                """
                INSERT INTO audio_files (
                    filename, file_path, file_size, created_at, modified_at,
                    status, status_updated_at, attempt_count, synced_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    filename=excluded.filename,
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

        record = self.get_file_by_path(file_path)
        if record is None:
            raise RuntimeError(f"Failed to ingest file metadata for {file_path}")
        return record

    @staticmethod
    def _adopt_relocated_row(
        cursor: sqlite3.Cursor,
        filename: str,
        file_size: int,
        modified_at: datetime,
        file_path: Path,
    ) -> None:
        """Re-point an existing row at a memo that moved.

        Keying on file_path cost us what ON CONFLICT(filename) used to do for
        free: without this, moving a memo (or a remount that changes the mount
        path) inserts a second row and transcribes it all over again, while the
        original row keeps a path that no longer resolves.

        Name and size alone are too weak a key. iOS reuses names (`5月16日
        22-00.m4a`), so a fresh recording that happened to match a deleted
        memo's name and byte count would be adopted onto that memo's row — and
        a row still marked 'completed' is dropped by prepare_targets, so the
        new audio would silently never be transcribed. Matching mtime too makes
        adoption mean "the same bytes, at a new path": a move preserves mtime, a
        different recording does not. The worst remaining case is a row whose
        original path is only temporarily unreachable being pointed at a
        byte-identical copy, which costs nothing and re-transcribes nothing.
        """

        target = str(file_path)
        already_known = cursor.execute(
            "SELECT 1 FROM audio_files WHERE file_path = ?", (target,)
        ).fetchone()
        if already_known:
            return

        rows = cursor.execute(
            """
            SELECT id, file_path FROM audio_files
            WHERE filename = ? AND file_size = ? AND modified_at = ?
            """,
            (filename, file_size, modified_at.strftime(ISO_FORMAT)),
        ).fetchall()
        for row in rows:
            if not Path(row["file_path"]).exists():
                cursor.execute(
                    "UPDATE audio_files SET file_path = ? WHERE id = ?",
                    (target, row["id"]),
                )
                return

    @staticmethod
    def _has_written_output(row: sqlite3.Row) -> bool:
        """True once this row has produced a transcript somewhere on disk."""

        return bool(
            (row["output_filename"] or "").strip() or (row["output_path"] or "").strip()
        )

    @staticmethod
    def _output_stamp(output_filename: Optional[str]) -> Optional[str]:
        """The YYYYMMDD_HHMMSS stamp an output filename carries, if any."""

        if not output_filename:
            return None
        match = _OUTPUT_STAMP_RE.search(output_filename)
        return match.group(1) if match else None

    def backfill_created_at_from_filename(self) -> List[str]:
        """Repair created_at on rows stored before it meant 'recording start'.

        Rows synced under the old st_ctime basis are never revisited by
        ingest_file (sync skips paths already in the database), so they would
        keep mixing two clocks forever. Filename-only — a regex, no I/O — so it
        is cheap enough to run on every sync; names carrying no timestamp are
        left alone.

        A row whose transcript is already written is a different matter: its
        created_at is baked into the note name on disk
        (`voice-memo_<YYYYMMDD_HHMMSS>_<title>.md`), so moving it would leave
        the database unable to name a file it produced. Those rows are repaired
        only when the new timestamp is the one their own output_filename
        already carries — i.e. when the repair pulls the row back into line with
        the disk instead of away from it.
        """

        repaired: List[str] = []
        with self.connect() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT id, filename, modified_at, created_at, output_path, "
                "output_filename FROM audio_files"
            ).fetchall()
            for row in rows:
                try:
                    modified_at = datetime.strptime(row["modified_at"], ISO_FORMAT)
                except (TypeError, ValueError):
                    continue
                started = recording_start_from_filename(row["filename"], modified_at)
                if started is None:
                    continue
                stamp = started.strftime(ISO_FORMAT)
                if stamp == row["created_at"]:
                    continue
                if self._has_written_output(row) and self._output_stamp(
                    row["output_filename"]
                ) != started.strftime(OUTPUT_STAMP_FORMAT):
                    continue
                cursor.execute(
                    "UPDATE audio_files SET created_at = ? WHERE id = ?",
                    (stamp, row["id"]),
                )
                repaired.append(f"{row['filename']}: {row['created_at']} -> {stamp}")
            if repaired:
                conn.commit()
        return repaired

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
        """Fetch candidate records based on filters.

        With no status asked for, errored rows are held back: a retry costs a
        paid API call on a file that already failed, so it has to be asked for
        by name (`--db-status error`).
        """

        query = "SELECT * FROM audio_files WHERE 1=1"
        params: List[object] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        else:
            query += " AND status != 'error'"

        if days is not None:
            time_limit = datetime.now() - timedelta(days=days)
            query += " AND datetime(created_at) >= datetime(?)"
            params.append(time_limit.strftime(ISO_FORMAT))

        query += " ORDER BY datetime(created_at) DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        elif offset:
            # SQLite only accepts OFFSET after a LIMIT; -1 is its "no cap" limit,
            # so --db-offset without --db-limit pages to the end of the table.
            query += " LIMIT -1"
        if offset:
            query += " OFFSET ?"
            params.append(offset)

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def reclaim_stale_processing(self) -> List[str]:
        """Hand rows abandoned in 'processing' back to 'pending'.

        A run killed mid-flight (Ctrl-C, a hung read over the network mount)
        never reaches mark_error, leaving its row in 'processing' forever —
        where --db-status pending can no longer see it.

        Age alone does not mean abandoned. A row whose owning process is still
        running here is skipped however long it has been held: taking it back
        would let a second run transcribe the same audio, paying the API twice
        and writing over the transcript the first run is about to produce.
        """

        cutoff = (
            datetime.now() - timedelta(minutes=STALE_PROCESSING_MINUTES)
        ).strftime(ISO_FORMAT)

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT file_path, processing_owner FROM audio_files "
                f"WHERE {_STALE_PREDICATE}",
                (cutoff,),
            )
            stale = [
                row["file_path"]
                for row in cursor.fetchall()
                if not _owner_is_alive(row["processing_owner"])
            ]
            if stale:
                placeholders = ",".join("?" for _ in stale)
                cursor.execute(
                    f"""
                    UPDATE audio_files
                    SET status = 'pending',
                        status_updated_at = ?,
                        processing_owner = NULL,
                        last_error = 'reclaimed: previous run never finished'
                    WHERE {_STALE_PREDICATE} AND file_path IN ({placeholders})
                    """,
                    (_now(), cutoff, *stale),
                )
                conn.commit()
            return stale

    def mark_processing(self, file_path: str) -> None:
        """Mark a file as currently being processed, by this run."""

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
                    processing_owner = ?,
                    last_error = NULL
                WHERE file_path = ?
                """,
                (now, now, _owner_token(), file_path),
            )
            conn.commit()

    def claim_output_filename(self, file_path: str, filename: str) -> bool:
        """Record a transcript filename for this row, unless another row holds it.

        The on-disk stub only reserves a name while the file is there; once the
        refine stage moves a transcript out, the name looks free again. Keeping
        the claim in the database lets a re-transcribe tell its own old
        transcript from a name another memo has since taken.
        """

        with self.connect() as conn:
            conn.isolation_level = None  # explicit transaction, see BEGIN below
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                taken = cursor.execute(
                    """
                    SELECT 1 FROM audio_files
                    WHERE output_filename = ? AND file_path != ?
                    LIMIT 1
                    """,
                    (filename, file_path),
                ).fetchone()
                if taken:
                    conn.execute("ROLLBACK")
                    return False
                cursor.execute(
                    "UPDATE audio_files SET output_filename = ? WHERE file_path = ?",
                    (filename, file_path),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def mark_completed(self, file_path: str, output_path: Path) -> None:
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
                    processing_owner = NULL,
                    last_error = NULL
                WHERE file_path = ?
                """,
                (
                    _now(),
                    str(output_path.parent),
                    output_path.name,
                    file_path,
                ),
            )
            conn.commit()

    def mark_error(self, file_path: str, error: str) -> None:
        """Mark a file as failed with an error message."""

        with self.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE audio_files
                SET status = 'error',
                    status_updated_at = ?,
                    processing_owner = NULL,
                    last_error = ?
                WHERE file_path = ?
                """,
                (_now(), error[:1000], file_path),
            )
            conn.commit()
