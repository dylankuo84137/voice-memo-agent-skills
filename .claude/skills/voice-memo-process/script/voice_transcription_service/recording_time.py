"""Resolve the true recording start time for a voice memo."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# iOS Voice Memos names a file after the moment recording STARTED, carrying no
# year: "6月25日 22-48.m4a", "3月27日08-29與施志恆討論智慧化氣候校園計畫.m4a".
_FILENAME_PATTERN = re.compile(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2})-(\d{2})")

_FFPROBE_TIMEOUT = 120

# How far behind mtime a filename-derived start may sit and still have its year
# believed. A memo is written when recording stops and may be re-saved by cloud
# sync a couple of days later (the longest real lag observed here is ~62h), so
# the window is generous; past it, mtime has clearly been detached from the
# recording and says nothing about which year the name refers to.
_MAX_SAVE_LAG = timedelta(days=7)


def resolve_recording_start(file_path: Path, modified_at: datetime) -> datetime:
    """Best-effort recording start time.

    Tried in cost order: the filename (no I/O), the container metadata (reads
    the audio), then the file's mtime as a last resort. The filename step
    abstains when it cannot tell which year the name belongs to, so the later,
    year-bearing sources get their turn instead of being overridden by a guess.
    """

    return (
        recording_start_from_filename(file_path.name, modified_at)
        or _from_ffprobe(file_path)
        or modified_at
    )


def recording_start_from_filename(
    filename: str, modified_at: datetime
) -> Optional[datetime]:
    """Recording start read off the filename alone.

    None when the name carries no timestamp, and also when it carries one whose
    year cannot be pinned down — the caller gets to say 'unknown' rather than a
    guess dressed as a reading.
    """

    match = _FILENAME_PATTERN.search(filename)
    if not match:
        return None

    month, day, hour, minute = (int(group) for group in match.groups())

    # No year in the name, but a memo is always saved on or after it was
    # recorded: take mtime's year, stepping back one if that lands in the future.
    for year in (modified_at.year, modified_at.year - 1):
        try:
            candidate = datetime(year, month, day, hour, minute)
        except ValueError:
            # Invalid in this year only — Feb 29 against a non-leap year still
            # has to be tried against the other one before giving up.
            continue
        if candidate > modified_at:
            continue
        if modified_at - candidate > _MAX_SAVE_LAG:
            # mtime is the only witness to the year, and this one is too far
            # from the recording to be one — re-downloaded from cloud storage,
            # restored from a backup, copied without -p. The name matches this
            # month-day in *every* year, so the nearest one is a guess that
            # would be stamped in as fact: abstain and let the caller fall
            # through to metadata that does carry a year.
            return None
        return candidate
    return None


def _from_ffprobe(file_path: Path) -> Optional[datetime]:
    """Derive the start from the container's end-of-recording timestamp."""

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:format_tags=creation_time",
                "-of",
                "json",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # ffprobe missing, or the read timed out

    if proc.returncode != 0:
        return None

    try:
        fmt = json.loads(proc.stdout)["format"]
        # QuickTime stamps creation_time when recording STOPS, in UTC.
        stopped = datetime.fromisoformat(
            fmt["tags"]["creation_time"].replace("Z", "+00:00")
        )
        if stopped.tzinfo is None:
            # Some muxers drop the 'Z'; the value is still UTC, and leaving it
            # naive would make astimezone() below read it as local time.
            stopped = stopped.replace(tzinfo=timezone.utc)
        duration = float(fmt["duration"])
    except (KeyError, TypeError, ValueError):
        return None

    started = stopped - timedelta(seconds=duration)
    return started.astimezone().replace(tzinfo=None)
